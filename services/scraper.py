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
    
    # Регулярка для поиск... (оставляем как было)
    TICKER_PATTERN = re.compile(r'\b[A-Z0-9]{2,8}\b')
    IGNORED_WORDS = {"GATE", "USDT", "BTC", "ETH", "WILL", "DELIST", "AND", "THE", "FOR", "TRADING", "PAIR", "SPOT", "CONTRACT", "TIME", "UTC"}

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
        return html

    async def check_delistings_blog(self):
        """
        1. Парсит страницу делистингов.
        2. Сохраняет ВСЕ найденные тикеры в таблицу DelistingEvent.
        3. Проверяет пересечение DelistingEvent и MonitoredPair -> создает Signal.
        """
        logger.info("Проверка блога Gate.io на делистинги...")
        
        try:
            html = await self._fetch_html(self.GATE_DELIST_URL)
            soup = BeautifulSoup(html, 'html.parser')
            links = soup.find_all('a', href=re.compile(r'/announcements/article/'))
            
            logger.info(f"Найдено {len(links)} статей. Анализ...")
            
            new_events_count = 0
            
            async with self.session_factory() as session:
                # 1. Сохраняем события в базу
                for link in links:
                    title = link.get_text(strip=True)
                    url = f"https://www.gate.io{link['href']}" if link['href'].startswith('/') else link['href']
                    
                    # Ищем тикеры
                    candidates = set(self.TICKER_PATTERN.findall(title.upper()))
                    candidates = candidates - self.IGNORED_WORDS
                    
                    for ticker in candidates:
                        # Проверяем, есть ли уже такой ивент
                        stmt = select(DelistingEvent).where(
                            DelistingEvent.exchange == "GATEIO",
                            DelistingEvent.symbol == ticker,
                            DelistingEvent.announcement_url == url
                        )
                        existing = (await session.execute(stmt)).first()
                        
                        if not existing:
                            event = DelistingEvent(
                                exchange="GATEIO",
                                symbol=ticker,
                                announcement_title=title,
                                announcement_url=url
                            )
                            session.add(event)
                            new_events_count += 1
                
                if new_events_count > 0:
                    await session.commit()
                    logger.success(f"Добавлено {new_events_count} новых записей о делистинге в Базу Знаний.")
                
                # 2. Матчинг: Ищем пересечения "Наши активные пары" <-> "База делистингов"
                # Получаем активные пары
                active_pairs_result = await session.execute(select(MonitoredPair).where(MonitoredPair.monitoring_status == "active"))
                active_pairs = active_pairs_result.scalars().all()
                
                signals_created = 0
                for pair in active_pairs:
                    # Базовая валюта пары (BTC/USDT -> BTC)
                    # Упрощенно. В идеале парсить пару полностью.
                    base_currency = pair.symbol.split('/')[0]
                    
                    # Ищем, есть ли для этой базовой валюты активный делистинг
                    # (можно добавить условие "свежий", например за последние 7 дней, но пока ищем любой)
                    stmt = select(DelistingEvent).where(
                        DelistingEvent.exchange == "GATEIO", # Или pair.exchange (если нормализован)
                        DelistingEvent.symbol == base_currency
                    )
                    evidence = (await session.execute(stmt)).scalars().first()
                    
                    if evidence:
                        # Если нашли - проверяем, не стоит ли уже риск статус
                        if pair.risk_level != RiskLevel.DELISTING_PLANNED:
                            pair.risk_level = RiskLevel.DELISTING_PLANNED
                            session.add(pair)
                            
                            # Генерируем сигнал
                            msg = f"⚠️ DELISTING WARNING! Pair: {pair.symbol}. found in article: {evidence.announcement_title}"
                            
                            # Проверяем дубликат сигнала
                            sig_check = select(Signal).where(
                                Signal.type == SignalType.DELISTING_WARNING,
                                Signal.raw_message == msg
                            )
                            if not (await session.execute(sig_check)).first():
                                session.add(Signal(type=SignalType.DELISTING_WARNING, raw_message=msg))
                                signals_created += 1

                if signals_created > 0 or new_events_count > 0:
                    await session.commit()
                    if signals_created:
                        logger.warning(f"Сгенерировано {signals_created} алертов по делистингу!")

        except Exception as e:
            logger.error(f"Ошибка при парсинге блога: {e}")

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
                                    session.add(Signal(type=SignalType.RISK_NEW, raw_message=f"API Status Change: {pair.symbol} is UNTRADABLE"))
                                    signals += 1
                                    
                        if signals > 0:
                            await session.commit()
                            logger.info(f"Обновлено статусов из API: {signals}")

        except Exception as e:
            logger.error(f"Ошибка API check: {e}")
