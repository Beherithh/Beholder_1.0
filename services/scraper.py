import asyncio
import re
import time
from loguru import logger
from typing import List, Set
from sqlmodel import select
from datetime import datetime
from bs4 import BeautifulSoup

import httpx 

from database.models import (
    MonitoredPair, Signal, SignalType, RiskLevel, AppSettings, DelistingEvent,
    DelistingEventType
)
from database.core import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from services.web_scraper import WebScraper
from services.article_parser import ArticleParser

class ScraperService:
    """
    Сервис для мониторинга внешних источников (блог для делистингов, API для рисков).
    """

    GATE_DELIST_URL = "https://www.gate.io/announcements/delisted"
    MEXC_DELIST_URL = "https://www.mexc.com/announcements/delistings/spot-18"
    BINANCE_DELIST_URL = "https://www.binance.com/en/support/announcement/delisting?c=161&navId=161"
    KUCOIN_DELIST_URL = "https://www.kucoin.com/announcement/delistings"
    
    # API endpoints for ST/Risk checks
    API_SOURCES = [
        {
            "name": "GATEIO",
            "url": "https://api.gateio.ws/api/v4/spot/currency_pairs",
            "symbol_key": "id",           # Field for symbol (e.g., "BTC_USDT")
            "st_key": "st_tag",           # Field for ST status (True/False or string)
        },
        {
            "name": "MEXC",
            "url": "https://api.mexc.com/api/v3/exchangeInfo",
            "symbol_key": "symbol",       # Field for symbol "BTCUSDT"
            "st_key": "st",               # TRUE = ST tag assigned (risk)
        },
        {
            "name": "KUCOIN",
            "url": "https://api.kucoin.com/api/v2/symbols",
            "symbol_key": "symbol",       # Field for symbol (e.g., "BTC-USDT")
            "st_key": "st",               # Field for ST status
        },
    ]

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.web_scraper = WebScraper()
        self.article_parser = ArticleParser()
    
    async def _update_pair_risk(self, session, pair: MonitoredPair, new_risk: RiskLevel, 
                                 signal_type: SignalType, msg: str) -> bool:
        """
        Универсальный метод обновления риска пары.
        Использует RiskLevel.priority для предотвращения понижения.
        Returns True if something changed (risk level OR new signal created).
        """
        changed = False

        # 1. Обновляем уровень риска, если он повысился
        if new_risk.priority > pair.risk_level.priority:
            pair.risk_level = new_risk
            session.add(pair)
            changed = True
            
            # 2. Отправляем уведомление только при ПОВЫШЕНИИ риска
            if new_risk != RiskLevel.NORMAL:
                # Check for duplicate signal
                sig_check = select(Signal).where(
                    Signal.type == signal_type, 
                    Signal.raw_message == msg,
                    Signal.is_sent == True
                )
                existing_sig = (await session.execute(sig_check)).first()
                
                if not existing_sig:
                    logger.warning(f"Creating NEW signal (risk increased): {msg}")
                    new_sig = Signal(type=signal_type, raw_message=msg)
                    session.add(new_sig)
                    await session.commit()
                    await session.refresh(new_sig)
                    
                    # Отправка в Telegram
                    from services.notifications import send_and_log_signal
                    asyncio.create_task(send_and_log_signal(new_sig.id, msg, prefix=""))
                else:
                    logger.info(f"Signal already exists, skipping: {msg[:100]}...")

        return changed
    
    async def check_binance_telegram_channel(self) -> int:
        """
        Читает последние сообщения из канала @binance_announcements через Pyrogram.
        Возвращает количество найденных новых событий.
        """
        try:
            from pyrogram import Client
        except ImportError:
            logger.error("Pyrogram не установлен. Используйте: uv add pyrogram")
            return 0
        
        # Получаем конфиг через ConfigService
        from services.system import get_config_service
        tg_conf = await get_config_service().get_telegram_config()
        
        if not tg_conf.api_id or not tg_conf.api_hash:
            logger.warning("Telegram API credentials не настроены. Пропуск проверки @binance_announcements")
            return 0
            
        async with self.session_factory() as session:
            # Get last processed message ID
            last_msg_id_setting = await session.get(AppSettings, "binance_tg_last_message_id")
            last_msg_id = int(last_msg_id_setting.value) if last_msg_id_setting and last_msg_id_setting.value else 0
            
            logger.info(f"Checking @binance_announcements (last message ID: {last_msg_id})...")
            
            new_events = 0
            latest_id = last_msg_id
            
            try:
                # Create Pyrogram client
                app = Client(
                    "beholder_telegram",
                    api_id=int(tg_conf.api_id),
                    api_hash=tg_conf.api_hash,
                    workdir="."
                )
                
                async with app:
                    # Read last 100 messages from channel
                    messages_count = 0
                    async for message in app.get_chat_history("binance_announcements", limit=100):
                        messages_count += 1
                        
                        if message.id <= last_msg_id:
                            break  # Already processed
                        
                        if message.id > latest_id:
                            latest_id = message.id
                        
                        # Support both text and caption (for images)
                        content = message.text or message.caption or ""
                        
                        if not content:
                            continue
                        
                        text_lower = content.lower()
                        
                        # Check ignore keywords first
                        if any(kw in text_lower for kw in self.article_parser.IGNORE_KEYWORDS):
                            continue
                        
                        # Check for delisting OR ST/Monitoring Tag keywords
                        is_relevant = any(k in text_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS) or \
                                      any(k.lower() in text_lower for k in self.article_parser.ST_TRIGGER_KEYWORDS)
                        
                        if not is_relevant:
                            continue
                        
                        logger.info(f"[BINANCE-TG] Processing message #{message.id}: {content[:100]}...")
                        
                        # Extract pairs using ArticleParser
                        pairs = self.article_parser.extract_pairs_from_text(content)
                        
                        if not pairs:
                            logger.debug(f"[BINANCE-TG] No pairs found in message #{message.id}")
                            continue
                        
                        # Determine event type
                        event_type = DelistingEventType.DELISTING if any(k in text_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS) else DelistingEventType.ST
                        
                        # Store in database
                        for symbol in pairs:
                            # Check if already exists
                            stmt = select(DelistingEvent).where(
                                DelistingEvent.exchange == "BINANCE",
                                DelistingEvent.symbol == symbol,
                                DelistingEvent.announcement_url == f"https://t.me/binance_announcements/{message.id}"
                            )
                            existing = (await session.execute(stmt)).first()
                            
                            if not existing:
                                event = DelistingEvent(
                                    exchange="BINANCE",
                                    symbol=symbol,
                                    announcement_title=content[:200],  # First 200 chars as title
                                    announcement_url=f"https://t.me/binance_announcements/{message.id}",
                                    type=event_type
                                )
                                session.add(event)
                                new_events += 1
                                event_label = "delisting" if event_type == DelistingEventType.DELISTING else "ST/Monitoring Tag"
                                logger.info(f"[BINANCE-TG] New {event_label}: {symbol}")
                        
                        await session.commit()
                
                # Update last processed message ID
                if latest_id > last_msg_id:
                    if not last_msg_id_setting:
                        last_msg_id_setting = AppSettings(key="binance_tg_last_message_id", value=str(latest_id))
                        session.add(last_msg_id_setting)
                    else:
                        last_msg_id_setting.value = str(latest_id)
                    await session.commit()
                    logger.info(f"[BINANCE-TG] Updated last message ID to {latest_id}")
                
                logger.info(f"[BINANCE-TG] Scanned {messages_count} messages. Found {new_events} new delisting events.")
                return new_events
                
            except Exception as e:
                logger.error(f"[BINANCE-TG] Error reading channel: {e}")
                return 0
    
    async def check_delistings_blog(self):
        """
        1. Парсит список статей для каждой настроенной биржи.
        2. Ищет ключевые слова "Delist" и др. в заголовках.
        3. Deep Scan: заходит внутрь и ищет пары.
        4. Сохраняет в БД.
        + match_monitored_pairs_with_events
        """
        
        sources = [
            {
                "name": "GATEIO",
                "url": self.GATE_DELIST_URL,
                "link_pattern": re.compile(r'/announcements/article/'),
                "domain": "https://www.gate.io"
            },
            {
                "name": "MEXC",
                "url": self.MEXC_DELIST_URL,
                "link_pattern": re.compile(r'/(announcements|support)/'), # MEXC links structure varies
                "domain": "https://www.mexc.com"
            },
            {
                "name": "BINANCE",
                "url": self.BINANCE_DELIST_URL,
                "link_pattern": re.compile(r'/announcement/'),
                "domain": "https://www.binance.com"
            },
            {
                "name": "KUCOIN",
                "url": self.KUCOIN_DELIST_URL,
                "link_pattern": re.compile(r'/announcement/'), # KuCoin uses direct links
                "domain": "https://www.kucoin.com"
            }
        ]
        
        logger.info("Запуск проверки делистингов (Deep Scan Mode)...")
        
        try:
            async with self.session_factory() as session:
                new_events_count = 0
                
                for source in sources:
                    ex_name = source["name"]
                    logger.info(f"Checking {ex_name} at {source['url']}...")
                    
                    try:
                        html = await self.web_scraper.fetch_html(source["url"])
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Ищем все ссылки, подходящие под паттерн статьи
                        raw_links = soup.find_all('a', href=source["link_pattern"])
                        
                        # Уникализация ссылок
                        unique_links = {}
                        for link in raw_links:
                            href = link['href']
                            # Нормализация URL
                            if href.startswith('/'):
                                full_url = f"{source['domain']}{href}"
                            elif href.startswith('http'):
                                full_url = href
                            else:
                                continue
                                
                            # Пропускаем саму главную страницу списка и пагинацию
                            if full_url.rstrip('/') == source["url"].rstrip('/') or "/list/" in full_url:
                                continue
                                
                            title = link.get_text(strip=True)
                            if full_url not in unique_links and title:
                                unique_links[full_url] = title
                        
                        # Дополнительный поиск для KuCoin: данные часто скрыты в <script> (JSON state)
                        if ex_name == "KUCOIN":
                            scripts = soup.find_all('script')
                            for script in scripts:
                                content = script.string
                                if not content or '"records":[' not in content:
                                    continue
                                
                                matches = re.finditer(r'\{"id":\d+,"title":"([^"]+)".*?"path":"([^"]+)"', content)
                                for match in matches:
                                    item_title = match.group(1)
                                    item_path = match.group(2)
                                    
                                    if not item_path.startswith('http'):
                                        item_url = f"{source['domain']}/announcement{item_path}"
                                    else:
                                        item_url = item_path
                                        
                                    if item_url not in unique_links:
                                        unique_links[item_url] = item_title
                                        logger.debug(f"[KUCOIN-JS] Found article: {item_title}")
                        
                        logger.info(f"[{ex_name}] Found {len(unique_links)} candidate articles.")
                        
                        for url, title in unique_links.items():
                            # 1. Проверяем заголовок на наличие триггеров
                            title_lower = title.lower()
                            
                            # Проверяем на исключаемые слова (convert, futures)
                            if any(k in title_lower for k in self.article_parser.IGNORE_KEYWORDS):
                                continue
                                
                            # Проверяем на ключевые слова (Delisting или ST)
                            is_relevant = any(k in title_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS) or \
                                          any(k.lower() in title_lower for k in self.article_parser.ST_TRIGGER_KEYWORDS)
                            
                            if not is_relevant:
                                continue 
                                
                            # 1.1 Пропускаем, если этот URL уже был обработан ранее
                            stmt_url = select(DelistingEvent).where(DelistingEvent.announcement_url == url)
                            existing_url = (await session.execute(stmt_url)).first()
                            if existing_url:
                                continue

                            logger.info(f"[{ex_name}] Analyzing article: {title}")
                            
                            # 2. Заходим внутрь (Deep Scan)
                            article_html = await self.web_scraper.fetch_html(url)
                            affected_tokens = self.article_parser.extract_pairs_from_html(article_html, url)
                            
                            if not affected_tokens:
                                logger.warning(f"[{ex_name}] '{title}' - Pairs not found.")
                                continue
                                
                            # 2.1 Определяем тип события для сохранения в БД
                            if any(k in title_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS):
                                event_type = DelistingEventType.DELISTING
                            else:
                                event_type = DelistingEventType.ST
                            
                            # 3. Сохраняем найденное
                            for symbol in affected_tokens:
                                stmt = select(DelistingEvent).where(
                                    DelistingEvent.exchange == ex_name,
                                    DelistingEvent.symbol == symbol,
                                    DelistingEvent.announcement_url == url
                                )
                                existing = (await session.execute(stmt)).first()
                                
                                if not existing:
                                    event = DelistingEvent(
                                        exchange=ex_name,
                                        symbol=symbol,
                                        announcement_title=title,
                                        announcement_url=url,
                                        type=event_type
                                    )
                                    session.add(event)
                                    new_events_count += 1
                                    logger.info(f"Found event ({event_type}): {symbol} in {url}")

                    except Exception as ex_err:
                        logger.error(f"Error checking {ex_name}: {ex_err}")
                if new_events_count > 0:
                    await session.commit()
                    logger.success(f"Добавлено {new_events_count} новых записей о делистинге.")
                
                # 4. Матчинг с активными парами (вынесено в отдельный метод)
                await self.match_monitored_pairs_with_events(session)

        except Exception as e:
            logger.error(f"Global Scraper Error: {e}")

    async def match_monitored_pairs_with_events(self, session: AsyncSession):
        """
        Сравнивает все активные отслеживаемые пары с историей событий в БД.
        Этот метод работает быстро, так как не использует внешние запросы (Selenium/API).
        """
        logger.info("Матчинг активных пар с историей событий в БД...")
        
        active_pairs_result = await session.execute(select(MonitoredPair).where(MonitoredPair.monitoring_status == "active"))
        active_pairs = active_pairs_result.scalars().all()
        
        if not active_pairs:
            return

        # Собираем все базовые валюты для запроса
        bases = list({p.symbol.split('/')[0] for p in active_pairs})
        
        # Строим карту {base_currency: [events]}
        events_map = {}
        if bases:
            chunk_size = 500
            for i in range(0, len(bases), chunk_size):
                chunk = bases[i:i + chunk_size]
                stmt = select(DelistingEvent).where(DelistingEvent.symbol.in_(chunk))
                chunk_events = (await session.execute(stmt)).scalars().all()
                
                for ev in chunk_events:
                    if ev.symbol not in events_map:
                        events_map[ev.symbol] = []
                    events_map[ev.symbol].append(ev)

        pairs_updated = 0
        
        for pair in active_pairs:
            base_currency = pair.symbol.split('/')[0]
            events = events_map.get(base_currency, [])
            
            for evidence in events:
                # 1. Определяем тип события и источник (Direct vs Cross)
                is_direct = (evidence.exchange.upper() == pair.exchange.upper())
                
                new_risk = None
                signal_type = None
                msg_prefix = ""
                
                # Логика приоритетов на основе поля type из БД
                if evidence.type == DelistingEventType.DELISTING:
                    if is_direct:
                        new_risk = RiskLevel.DELISTING_PLANNED
                        signal_type = SignalType.DELISTING_WARNING
                        msg_prefix = "⚠️ DELISTING WARNING!"
                    else:
                        new_risk = RiskLevel.CROSS_DELISTING
                        signal_type = SignalType.DELISTING_WARNING
                        msg_prefix = "⚠️ CROSS-EXCHANGE DELISTING!"
                
                elif evidence.type == DelistingEventType.ST:
                    if is_direct:
                        new_risk = RiskLevel.RISK_ZONE
                        signal_type = SignalType.ST_WARNING
                        msg_prefix = "⚠️ ST WARNING!"
                    else:
                        new_risk = RiskLevel.CROSS_RISK
                        signal_type = SignalType.ST_WARNING
                        msg_prefix = "⚠️ CROSS-EXCHANGE ST WARNING!"
                
                # Используем универсальный метод обновления риска
                if new_risk:
                    trigger_text = ""
                    if evidence.type == DelistingEventType.ST and "API ST tag" in (evidence.announcement_title or ""):
                         # Извлекаем инфо о парах из заголовка ивента
                         trigger_text = f"\n {evidence.announcement_title}"

                    msg = f"{msg_prefix} Pair: {pair.symbol} Active in: {pair.source_label} \n Info from: {evidence.exchange}. Article: {evidence.announcement_url}{trigger_text}"
                    if await self._update_pair_risk(session, pair, new_risk, signal_type, msg):
                        pairs_updated += 1

        if pairs_updated > 0:
            await session.commit()
            logger.warning(f"Матчинг завершен: обновлено {pairs_updated} пар!")
        else:
            logger.info("Матчинг завершен: изменений не найдено.")
            
        return pairs_updated

    async def check_api_risks(self):
        """
        Проверка API бирж на статус ST/Risk.
        Унифицированный метод + Кросс-Алерты + Auto-Recovery.
        + match_monitored_pairs_with_events
        """
        logger.info("Проверка API на ST/Risk статусы (Direct & Cross)...")
        
        async with self.session_factory() as session:
            # 1. Загружаем активные пары
            db_pairs = (await session.execute(
                select(MonitoredPair).where(MonitoredPair.monitoring_status == "active")
            )).scalars().all()
            
            if not db_pairs:
                return

            signals_created = 0 # Используем как флаг изменений (не только сигналы, но и recovery)
            
            # 2. Собираем данные со всех API в единую структуру
            # all_api_data = { "GATEIO": {"BTC/USDT": {...data...}, ...}, "MEXC": {...} }
            all_api_data = {}
            quote_currencies = {"USDT", "BTC", "ETH", "BUSD", "BNB", "SOL", "USDC"}

            async with httpx.AsyncClient(timeout=30.0) as client:
                for source in self.API_SOURCES:
                    ex_name = source["name"]
                    logger.info(f"[{ex_name}] Fetching API: {source['url']}")
                    all_api_data[ex_name] = {}
                    
                    try:
                        resp = await client.get(source["url"])
                        if resp.status_code != 200:
                            logger.warning(f"[{ex_name}] API returned {resp.status_code}")
                            continue
                            
                        data = resp.json()
                        
                        # Normalize format:
                        # MEXC: {"symbols": [...]}
                        # KuCoin: {"data": [...]}
                        # Gate.io: [...]
                        if isinstance(data, dict):
                            if "symbols" in data:
                                items = data["symbols"]
                            elif "data" in data:
                                items = data["data"]
                            else:
                                logger.warning(f"[{ex_name}] Unexpected API response format (no symbols/data key)")
                                continue
                        elif isinstance(data, list):
                            items = data
                        else:
                            logger.warning(f"[{ex_name}] Unexpected API response format (not dict/list)")
                            continue
                        
                        # Build Normalize Map
                        for item in items:
                            symbol_key = source.get("symbol_key", "symbol")
                            raw_symbol = item.get(symbol_key, "")
                            if not raw_symbol:
                                continue
                            
                            # Нормализуем к формату БД: BTC/USDT
                            if "_" in raw_symbol:
                                # BTC_USDT -> BTC/USDT
                                normalized = raw_symbol.replace("_", "/")
                            elif "-" in raw_symbol:
                                # KuCoin: BTC-USDT -> BTC/USDT
                                normalized = raw_symbol.replace("-", "/")
                            elif "/" in raw_symbol:
                                # Уже в нужном формате
                                normalized = raw_symbol
                            else:
                                # BTCUSDT -> BTC/USDT (ищем известную quote currency в конце)
                                normalized = raw_symbol
                                for quote in quote_currencies:
                                    if raw_symbol.endswith(quote) and len(raw_symbol) > len(quote):
                                        base = raw_symbol[:-len(quote)]
                                        normalized = f"{base}/{quote}"
                                        break
                            
                            # Группируем данные по базовой валюте в all_api_data
                            base_currency = normalized.split('/')[0].upper()
                            if base_currency not in all_api_data[ex_name]:
                                all_api_data[ex_name][base_currency] = []
                            
                            all_api_data[ex_name][base_currency].append({
                                "symbol": normalized.upper(),
                                "item": item
                            })
                            
                    except Exception as api_err:
                        logger.error(f"[{ex_name}] API Fetch Error: {api_err}")

            # 3. Анализируем данные API
            for pair in db_pairs:
                current_ex = pair.exchange.upper()
                pair_symbol = pair.symbol.upper()
                base_currency = pair_symbol.split('/')[0]
                
                # --- A. Populating DelistingEvent from API (ST status) ---
                # Проверяем ВСЕ пары для данной монеты на каждой бирже на наличие ST тега
                for ex_name, ex_data in all_api_data.items():
                    # ex_data теперь имеет структуру { "BTC": [{"symbol": "BTC/USDT", "item": {...}}, ...], ... }
                    ticker_pairs = ex_data.get(base_currency, [])
                    
                    st_triggering_pairs = []
                    source_cfg = next((s for s in self.API_SOURCES if s["name"] == ex_name), {})
                    st_key = source_cfg.get("st_key", "st")
                    
                    for entry in ticker_pairs:
                        api_item = entry["item"]
                        st_value = api_item.get(st_key)
                        is_risk = (st_value == True or str(st_value).lower() == "true" or st_value == 1 or st_value == "1")
                        
                        if is_risk:
                            st_triggering_pairs.append(entry["symbol"])
                    
                    if st_triggering_pairs:
                        # Если нашли хоть одну пару с ST риском для этого тикера
                        trigger_info = ", ".join(st_triggering_pairs)
                        # Сохраняем ивент в БД (если еще нет)
                        stmt = select(DelistingEvent).where(
                            DelistingEvent.exchange == ex_name,
                            DelistingEvent.symbol == base_currency,
                            DelistingEvent.announcement_url == source_cfg.get("url", "API"),
                            DelistingEvent.type == DelistingEventType.ST
                        )
                        existing_event = (await session.execute(stmt)).scalars().first()
                        
                        event_title = f"API ST tag: {trigger_info}"
                        
                        if not existing_event:
                            new_event = DelistingEvent(
                                exchange=ex_name,
                                symbol=base_currency,
                                announcement_title=event_title,
                                announcement_url=source_cfg.get("url", "API"),
                                type=DelistingEventType.ST
                            )
                            session.add(new_event)
                            logger.info(f"[{ex_name}] New ST status detected for ticker {base_currency} (via {trigger_info})")
                        else:
                            # Обновляем заголовок, если список триггеров изменился
                            if existing_event.announcement_title != event_title:
                                existing_event.announcement_title = event_title
                                session.add(existing_event)

                # --- B. Recovery Logic (Unique to API) ---
                # Если хоть одна пара для тикера на родной бирже имеет ST=True -> риск остается.
                # Снимаем только если ВСЕ пары для этого тикера на родной бирже имеют ST=False.
                native_ticker_pairs = all_api_data.get(current_ex, {}).get(base_currency, [])
                if native_ticker_pairs and pair.risk_level == RiskLevel.RISK_ZONE:
                    source_cfg = next((s for s in self.API_SOURCES if s["name"] == current_ex), {})
                    st_key = source_cfg.get("st_key", "st")
                    
                    any_st_active = False
                    for entry in native_ticker_pairs:
                        st_value = entry["item"].get(st_key)
                        if (st_value == True or str(st_value).lower() == "true" or st_value == 1 or st_value == "1"):
                            any_st_active = True
                            break
                    
                    if not any_st_active:
                        pair.risk_level = RiskLevel.NORMAL
                        session.add(pair)
                        signals_created += 1 # Trigger commit/log
                        logger.info(f"[{current_ex}] {pair.symbol} - ST Cleared for ticker {base_currency} (Recovery).")

            # 4. Вызываем единый матчер для выставления алертов (Direct & Cross)
            # Он увидит новые записи в DelistingEvent и обновит риски/создаст сигналы.
            await session.commit() # Сначала сохраняем новые DelistingEvent
            await self.match_monitored_pairs_with_events(session)
            
            if signals_created > 0:
                await session.commit()

    async def check_all_risks(self):
        """
        Вызывает все проверки риска: блог и API.
        Перед проверкой автоматически синхронизирует список пар из файлов.
        """
        logger.info("=== Запуск полной проверки рисков Delistings + ST ===")
        
        # Автоматическая синхронизация файлов перед проверкой
        try:
            logger.info("Синхронизация списка пар из файлов...")
            from services.file_watcher import FileWatcherService
            watcher = FileWatcherService(get_session)
            stats = await watcher.sync_from_settings()
            logger.info(f"Синхронизация завершена: {stats}")
            
            # Быстрый матч с существующими событиями
            async with get_session() as session:
                matches = await self.match_monitored_pairs_with_events(session)
                logger.info(f"Найдено совпадений с историей: {matches}")
        except Exception as e:
            logger.error(f"Ошибка синхронизации файлов: {e}")
        
        # Основные проверки
        await self.check_binance_telegram_channel()  # Telegram channel (primary for Binance)
        await self.check_delistings_blog()  # Web scraping (fallback + other exchanges)
        await self.check_api_risks()
        logger.info("=== Полная проверка рисков Delistings + ST завершена ===")
