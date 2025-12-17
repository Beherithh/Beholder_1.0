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

from database.models import MonitoredPair, Signal, SignalType, RiskLevel, AppSettings, DelistingEvent
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
    
    # Регулярка для поиска ПАР в тексте (например: "ABC_USDT", "ABC/ETH")
    # Это гораздо надежнее, чем искать просто "ABC"
    # Исключены слова содержащие только цифры - (?![0-9]+\b)
    PAIR_PATTERN = re.compile(r'\b(?![0-9]+\b)([A-Z0-9]{1,10})[-_/\.]?(USDT|BTC|ETH|BUSD|BNB|SOL)?\b')

    def __init__(self, session_factory):
        self.session_factory = session_factory
    
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

            # 2. Remove elements by class/id (heuristics for sidebars/related)
            # Keywords: related, sidebar, menu, widget, recent, popular, recommended
            noise_pattern = re.compile(r'(related|sidebar|menu|widget|recent|popular|recommend|footer|header|cookie)', re.I)
            
            # We must convert iterator to list because we modify the tree
            for tag in list(soup.find_all(attrs={"class": noise_pattern})):
                tag.decompose()
                
            for tag in list(soup.find_all(attrs={"id": noise_pattern})):
                tag.decompose()
            
            # -------------------------------

            text = soup.get_text(" ", strip=True) # Получаем весь текст (он теперь чище)
            
            # Ищем пары вида XXX_USDT
            found_pairs = self.PAIR_PATTERN.findall(text) # Возвращает список кортежей [('TIME', 'USDT'), ('PLAN', 'ETH')]
            
            result = set()
            for base, quote in found_pairs:
                result.add(base)
            
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
                                
                            title = link.get_text(strip=True)
                            if full_url not in unique_links and title:
                                unique_links[full_url] = title
                        
                        logger.info(f"[{ex_name}] Found {len(unique_links)} candidate articles.")
                        
                        for url, title in unique_links.items():
                            # 1. Проверяем заголовок на наличие триггеров
                            title_lower = title.lower()
                            
                            # Проверяем на ключевые слова (Delisting)
                            is_delist = any(k in title_lower for k in self.DELIST_TRIGGER_KEYWORDS)
                            # Можно добавить логику для ST_TRIGGER_KEYWORDS, если нужно
                            
                            if not is_delist:
                                continue 
                                
                            logger.info(f"[{ex_name}] Analyzing article: {title}")
                            
                            # 2. Заходим внутрь (Deep Scan)
                            affected_tokens = await self._extract_pairs_from_article(url)
                            
                            if not affected_tokens:
                                logger.warning(f"[{ex_name}] '{title}' - Pairs not found.")
                                continue
                                
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
                                        announcement_url=url
                                    )
                                    session.add(event)
                                    new_events_count += 1
                                    logger.info(f"Found event: {symbol} in {url}")

                    except Exception as ex_err:
                        logger.error(f"Error checking {ex_name}: {ex_err}")

                if new_events_count > 0:
                    await session.commit()
                    logger.success(f"Добавлено {new_events_count} новых записей о делистинге.")
                
                # 4. Матчинг с активными парами (Generic logic for all exchanges)
                # Получаем все активные пары независимо от биржи (или можно фильтровать)
                active_pairs_result = await session.execute(select(MonitoredPair).where(MonitoredPair.monitoring_status == "active"))
                active_pairs = active_pairs_result.scalars().all()
                
                signals_created = 0
                
                # Pre-fetch events grouping by (exchange, symbol) optimized? 
                # Пока перебором, так как событий немного.
                
                for pair in active_pairs:
                    base_currency = pair.symbol.split('/')[0]
                    
                    # Поиск доказательств (Cross-Exchange)
                    # Ищем события делистинга/ST для этой монеты на ЛЮБОЙ бирже
                    stmt = select(DelistingEvent).where(
                        DelistingEvent.symbol == base_currency
                    )
                    events = (await session.execute(stmt)).scalars().all()
                    
                    for evidence in events:
                        title_lower = evidence.announcement_title.lower()
                        
                        # 1. Определяем тип события
                        is_delist = any(k in title_lower for k in self.DELIST_TRIGGER_KEYWORDS)
                        is_st = any(k in title_lower for k in self.ST_TRIGGER_KEYWORDS)
                        
                        # 2. Определяем тип источника (Direct vs Cross)
                        is_direct = (evidence.exchange.upper() == pair.exchange.upper())
                        
                        current_risk = pair.risk_level
                        new_risk = None
                        signal_type = None
                        msg_prefix = ""
                        
                        # Логика приоритетов (Highest wins)
                        # Hierarchy: DELISTING_PLANNED > RISK_ZONE > CROSS_DELISTING > CROSS_RISK > NORMAL
                        
                        if is_delist:
                            if is_direct:
                                new_risk = RiskLevel.DELISTING_PLANNED
                                signal_type = SignalType.DELISTING_WARNING
                                msg_prefix = "⚠️ DELISTING WARNING!"
                            else:
                                new_risk = RiskLevel.CROSS_DELISTING
                                signal_type = SignalType.DELISTING_WARNING # Use generic or create new type
                                msg_prefix = "⚠️ CROSS-EXCHANGE DELISTING!"
                        
                        elif is_st:
                            if is_direct:
                                new_risk = RiskLevel.RISK_ZONE
                                signal_type = SignalType.ST_WARNING
                                msg_prefix = "⚠️ ST WARNING!"
                            else:
                                new_risk = RiskLevel.CROSS_RISK
                                signal_type = SignalType.ST_WARNING
                                msg_prefix = "⚠️ CROSS-EXCHANGE RISK!"
                        else:
                            continue # Unknown event type

                        # Апгрейдим риск только если новый приоритет выше текущего
                        if new_risk and new_risk.priority > current_risk.priority:
                            pair.risk_level = new_risk
                            session.add(pair)
                            
                            # Генерируем сигнал
                            msg = f"{msg_prefix} Pair: {pair.symbol} ({pair.exchange}). Source: {evidence.exchange}. Article: {evidence.announcement_title}"
                            
                            sig_check = select(Signal).where(
                                Signal.type == signal_type,
                                Signal.raw_message == msg
                            )
                            if not (await session.execute(sig_check)).first():
                                session.add(Signal(type=signal_type, raw_message=msg))
                                signals_created += 1
                        
                        # Если риск не повышаем, но сигнал важный (например Cross-Delisting), может все равно стоит отправить алерт? 
                        # Пока оставим логику "только при повышении риска или если такого сигнала не было".
                        # Но выше проверка на дубликат сигнала уже есть.
                        # Если, например, у нас уже DELISTING_PLANNED, а пришел CROSS_DELISTING -> мы не меняем статус.
                        # Но алерт можно было бы и послать.
                        # Сейчас пошлем алерт только если повысили статус ИЛИ (можно переделать).
                        # Оставим пока strict state updates.

                if signals_created > 0:
                    await session.commit()
                    logger.warning(f"Сгенерировано {signals_created} алертов риска!")

        except Exception as e:
            logger.error(f"Global Scraper Error: {e}")

    async def check_api_risks(self):
        """
        Проверка API на статус 'st' (Risk) или 'delisting'.
        GET https://api.gateio.ws/api/v4/spot/currency_pairs
        """
        url = "https://api.gateio.ws/api/v4/spot/currency_pairs"
        logger.info("Проверка API статус 'trade_status'...")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    # data - список объектов
                    # { "id": "ETH_USDT", "base": "ETH", "quote": "USDT", "trade_status": "tradable", "sell_start": ... }
                    
                    async with self.session_factory() as session:
                        # Загружаем наши пары
                        db_pairs = (await session.execute(select(MonitoredPair).where(MonitoredPair.monitoring_status == "active"))).scalars().all()
                        db_map = {p.symbol.replace('/', '_'): p for p in db_pairs} # BTC/USDT -> BTC_USDT для маппинга с API
                        
                        signals = 0
                        for item in data:
                            pair_code = item.get("id")
                            trade_status = item.get("trade_status") # tradable, untradable, sellable
                            
                            if pair_code in db_map:
                                pair = db_map[pair_code]
                                # Логика определения риска по статусу API
                                if trade_status == 'untradable' and pair.risk_level != RiskLevel.DELISTING_PLANNED:
                                    pair.risk_level = RiskLevel.DELISTING_PLANNED
                                    session.add(pair)
                                    session.add(Signal(type=SignalType.ST_WARNING, raw_message=f"API Status Change: {pair.symbol} is UNTRADABLE"))
                                    signals += 1
                                    
                        if signals > 0:
                            await session.commit()
                            logger.info(f"Обновлено статусов из API: {signals}")

        except Exception as e:
            logger.error(f"Ошибка API check: {e}")
