import asyncio
import re
from loguru import logger
from typing import List, Set
from sqlmodel import select
from datetime import datetime
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

from database.models import (
    MonitoredPair, Signal, SignalType, RiskLevel, AppSettings, DelistingEvent,
    DelistingEventType
)
from database.core import get_session
from sqlalchemy.ext.asyncio import AsyncSession
import httpx # Still needed for API check

class ScraperService:
    """
    Сервис для мониторинга внешних источников (блог для делистингов, API для рисков).
    """

    GATE_DELIST_URL = "https://www.gate.io/announcements/delisted"
    MEXC_DELIST_URL = "https://www.mexc.com/announcements/delistings"
    
    DELIST_TRIGGER_KEYWORDS = {"delist", "oppos", "remov", "offline", "risk", "suspend"}
    ST_TRIGGER_KEYWORDS = {"st_tag", "ST Warning", "Assessment Zone"}
    IGNORE_KEYWORDS = {"convert", "future", "perpetual", "option"}
    
    # Регулярка для поиска ПАР в тексте (например: "ABC_USDT", "ABC/ETH", "ABCUSDT", "ICE")
    # Quote опциональна, чтобы захватывать и одиночные символы типа "ICE"
    # Исключены слова содержащие только цифры - (?![0-9]+\b)
    # Минимальная длина 2 символа, чтобы отсечь шум типа "A", "I"
    QUOTE_CURRENCIES = ("USDT", "BTC", "ETH", "BUSD", "BNB", "SOL", "USDC")
    PAIR_PATTERN = re.compile(
        r'\b(?![0-9]+\b)([A-Z0-9]{2,11})[-_/\.]?(USDT|BTC|ETH|BUSD|BNB|SOL|USDC)?\b'
    )

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
    ]

    def __init__(self, session_factory):
        self.session_factory = session_factory
    
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
            
        # 2. Проверяем, нужно ли отправить уведомление (даже если уровень риска тот же, но контекст/сообщение новые)
        if new_risk != RiskLevel.NORMAL:
            # Check for duplicate signal
            sig_check = select(Signal).where(Signal.type == signal_type, Signal.raw_message == msg)
            existing_sig = (await session.execute(sig_check)).first()
            
            if not existing_sig:
                logger.warning(f"Creating NEW signal: {msg}")
                session.add(Signal(type=signal_type, raw_message=msg))
                
                # Отправка в Telegram
                from services.system import get_telegram_service
                asyncio.create_task(get_telegram_service().send_message(f"<b>[ALERT]</b> {msg}"))
                
                changed = True
            else:
                logger.info(f"Signal already exists, skipping: {msg[:100]}...")

        return changed
    
    async def _fetch_html(self, url: str) -> str:
        """
        Использует Selenium для обхода защиты (403/Cloudflare).
        """
        def _selenium_get():
            options = Options()
            options.add_argument("--headless=new") # Запуск без окна
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            
            # Инициализация драйвера (автоматически скачает нужную версию)
            driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
            try:
                driver.get(url)
                # Даем время на выполнение JS (Cloudflare challenge и т.д.)
                driver.implicitly_wait(5) 
                return driver.page_source
            finally:
                driver.quit()

        # Запускаем в отдельном потоке, так как selenium - синхронный
        logger.info("Запуск Chrome через Selenium...")
        html = await asyncio.get_running_loop().run_in_executor(None, _selenium_get)
        if not html:
            raise ValueError("Selenium вернул пустой HTML")
        return html

    async def _extract_pairs_from_article(self, url: str) -> Set[str]:
        """
        Заходит внутрь статьи и ищет торговые пары.
        Возвращает набор найденных БАЗОВЫХ валют (например {"TIME", "PLAN"} из "TIME_USDT")
        """
        logger.info(f"Deep scan: {url}")
        try:
            html = await self._fetch_html(url) # Используем тот же Selenium метод
            soup = BeautifulSoup(html, 'html.parser')
            
            # --- Cleaning DOM from Noise ---
            # 1. Remove standard non-content tags
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
                tag.decompose()

            # 2. Heuristics for sidebars/related via keywords in class/id
            noise_pattern = re.compile(
                r'(related|sidebar|menu|widget|recent|popular|recommend|footer|header|cookie|social|share|comment|banner|ad-|promo|breadcrumb|nav|tab|pagenavi)', 
                re.I
            )
            
            for tag in list(soup.find_all(attrs={"class": noise_pattern})):
                tag.decompose()
                
            for tag in list(soup.find_all(attrs={"id": noise_pattern})):
                tag.decompose()

            # 3. Targeted extraction of main content (Exchange specific)
            # This is the most effective way to avoid sidebar noise
            main_content = None
            
            if "mexc.com" in url:
                # MEXC specific: usually div#content or div.article_articleContent...
                main_content = soup.find('div', id='content') or soup.find('div', class_=re.compile(r'articleContent'))
            elif "gate." in url:
                # Gate specific: usually div#article-detail-container
                main_content = soup.find('div', id='article-detail-container')
            
            # If we found a specific container, use it. Otherwise fall back to body.
            if main_content:
                soup = main_content
                logger.info(f"Targeted main content container found for {url}")
            else:
                # Heuristic: Find H1 and take its container if it's large enough
                h1 = soup.find('h1')
                if h1:
                    parent = h1.parent
                    # If parent is just a wrapper, maybe go one level up
                    if len(parent.get_text()) < 200 and parent.parent:
                        parent = parent.parent
                    soup = parent
            
            text = soup.get_text(" ", strip=True) 
            
            # Ищем пары вида XXX_USDT или просто XX
            raw_matches = self.PAIR_PATTERN.finditer(text)
            
            result = set()
            for match in raw_matches:
                base = match.group(1)
                quote = match.group(2)
                
                base_upper = base.upper()
                
                # Пропускаем, если base - это ключевое слово (защита от ложных срабатываний)
                if base_upper.lower() in self.ST_TRIGGER_KEYWORDS or \
                   any(k.upper() == base_upper for k in self.IGNORE_KEYWORDS) or \
                   base_upper in ("TRADING", "DELISTING", "PAIR", "LIST", "SUPPORT", "ZONE"):
                    continue
                # Постобработка: если quote не была захвачена отдельно, 
                # проверяем не застряла ли она в конце base (например ICEUSDT)
                if not quote:
                    for q in self.QUOTE_CURRENCIES:
                        if base_upper.endswith(q) and len(base_upper) > len(q):
                            base_upper = base_upper[:-len(q)]
                            break
                result.add(base_upper)
            
            return result
        except Exception as e:
            # logger.error(f"Failed to scan article {url}: {e}") # Reduce log noise
            return set()

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
                        html = await self._fetch_html(source["url"])
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
                                
                            # Пропускаем саму главную страницу списка, чтобы не скрапить всё подряд
                            if full_url.rstrip('/') == source["url"].rstrip('/'):
                                continue
                                
                            title = link.get_text(strip=True)
                            if full_url not in unique_links and title:
                                unique_links[full_url] = title
                        
                        logger.info(f"[{ex_name}] Found {len(unique_links)} candidate articles.")
                        
                        for url, title in unique_links.items():
                            # 1. Проверяем заголовок на наличие триггеров
                            title_lower = title.lower()
                            
                            # Проверяем на исключаемые слова (convert, futures)
                            if any(k in title_lower for k in self.IGNORE_KEYWORDS):
                                continue
                                
                            # Проверяем на ключевые слова (Delisting или ST)
                            is_relevant = any(k in title_lower for k in self.DELIST_TRIGGER_KEYWORDS) or \
                                          any(k.lower() in title_lower for k in self.ST_TRIGGER_KEYWORDS)
                            
                            if not is_relevant:
                                continue 
                                
                            # 1.1 Пропускаем, если этот URL уже был обработан ранее
                            stmt_url = select(DelistingEvent).where(DelistingEvent.announcement_url == url)
                            existing_url = (await session.execute(stmt_url)).first()
                            if existing_url:
                                # logger.info(f"[{ex_name}] Skipping already processed article: {title}")
                                continue

                            logger.info(f"[{ex_name}] Analyzing article: {title}")
                            
                            # 2. Заходим внутрь (Deep Scan)
                            affected_tokens = await self._extract_pairs_from_article(url)
                            
                            if not affected_tokens:
                                logger.warning(f"[{ex_name}] '{title}' - Pairs not found.")
                                continue
                                
                            # 2.1 Определяем тип события для сохранения в БД
                            event_type = DelistingEventType.DELISTING if is_relevant and any(k in title_lower for k in self.DELIST_TRIGGER_KEYWORDS) else DelistingEventType.ST
                            
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
                
                # SQLite limit is usually 999 vars, splitting chunks if necessary or just fetch all recent?
                # Для надежности и простоты, если база небольшая, сделаем IN. Если монет тысячи - надо чанками.
                # Пока предполагаем разумное количество (<500).
        
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

        signals_created = 0
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
                        msg_prefix = "⚠️ CROSS-EXCHANGE RISK!"
                
                # Используем универсальный метод обновления риска
                if new_risk:
                    msg = f"{msg_prefix} Pair: {pair.symbol} ({pair.source_label}). Active: {evidence.exchange}. Article: {evidence.announcement_url}"
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
                        
                        # Normalize format - MEXC returns {"symbols": [...]} while Gate returns [...]
                        if isinstance(data, dict) and "symbols" in data:
                            items = data["symbols"]
                        elif isinstance(data, list):
                            items = data
                        else:
                            logger.warning(f"[{ex_name}] Unexpected API response format")
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
                            
                            all_api_data[ex_name][normalized.upper()] = item
                            
                    except Exception as api_err:
                        logger.error(f"[{ex_name}] API Fetch Error: {api_err}")

            # 3. Анализируем данные API
            for pair in db_pairs:
                current_ex = pair.exchange.upper()
                pair_symbol = pair.symbol.upper()
                base_currency = pair_symbol.split('/')[0]
                
                # --- A. Populating DelistingEvent from API (ST status) ---
                # Проверяем все биржи на наличие ST тега для нашей монеты
                for ex_name, ex_data in all_api_data.items():
                    api_item = ex_data.get(pair_symbol)
                    if api_item:
                        source_cfg = next((s for s in self.API_SOURCES if s["name"] == ex_name), {})
                        st_key = source_cfg.get("st_key", "st")
                        st_value = api_item.get(st_key)
                        
                        is_risk = (st_value == True or str(st_value).lower() == "true" or st_value == 1 or st_value == "1")
                        
                        if is_risk:
                            # Сохраняем ивент в БД (если еще нет)
                            # Это создаст базу для метода match_monitored_pairs_with_events
                            stmt = select(DelistingEvent).where(
                                DelistingEvent.exchange == ex_name,
                                DelistingEvent.symbol == base_currency,
                                DelistingEvent.announcement_url == source_cfg.get("url", "API"),
                                DelistingEvent.type == DelistingEventType.ST
                            )
                            if not (await session.execute(stmt)).first():
                                new_event = DelistingEvent(
                                    exchange=ex_name,
                                    symbol=base_currency,
                                    announcement_title="API Risk Status",
                                    announcement_url=source_cfg.get("url", "API"),
                                    type=DelistingEventType.ST
                                )
                                session.add(new_event)
                                logger.info(f"[{ex_name}] New ST status detected for {base_currency}")

                # --- B. Recovery Logic (Unique to API) ---
                # Если пара есть на родной бирже, и статус ST=False, и текущий риск RISK_ZONE -> снимаем
                native_data = all_api_data.get(current_ex, {}).get(pair_symbol)
                if native_data and pair.risk_level == RiskLevel.RISK_ZONE:
                    source_cfg = next((s for s in self.API_SOURCES if s["name"] == current_ex), {})
                    st_key = source_cfg.get("st_key", "st")
                    st_value = native_data.get(st_key)
                    is_risk = (st_value == True or str(st_value).lower() == "true" or st_value == 1 or st_value == "1")
                    
                    if not is_risk:
                        pair.risk_level = RiskLevel.NORMAL
                        session.add(pair)
                        signals_created += 1 # Trigger commit/log
                        logger.info(f"[{current_ex}] {pair.symbol} - ST Cleared (Recovery).")

            # 4. Вызываем единый матчер для выставления алертов (Direct & Cross)
            # Он увидит новые записи в DelistingEvent и обновит риски/создаст сигналы.
            await session.commit() # Сначала сохраняем новые DelistingEvent
            await self.match_monitored_pairs_with_events(session)
            
            if signals_created > 0:
                await session.commit()

    async def check_all_risks(self):
        """
        Вызывает все проверки риска: блог и API.
        """
        logger.info("=== Запуск полной проверки рисков Delistings + ST ===")
        await self.check_delistings_blog()
        await self.check_api_risks()
        logger.info("=== Полная проверка рисков Delistings + ST завершена ===")

