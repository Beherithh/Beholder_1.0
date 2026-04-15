from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from loguru import logger
from sqlmodel import select, delete

from database.models import (
    MonitoredPair, Signal, SignalType, RiskLevel, DelistingEvent,
    DelistingEventType
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.web_scraper import WebScraper
from services.article_parser import ArticleParser
from services.api_risk_checker import ApiRiskCheckerService
from services.telegram_monitor import TelegramMonitorService
from services.blog_scraper import BlogScraperService
from services.file_watcher import FileWatcherService
from services.config import ConfigService
from database.core import get_session

if TYPE_CHECKING:
    from services.notifications import NotificationService

class ScraperService:
    """
    Сервис-координатор для мониторинга внешних источников.
    Объединяет работу:
    - TelegramMonitorService (Telegram)
    - BlogScraperService (Блоги бирж)
    - ApiRiskCheckerService (API бирж)
    """

    def __init__(self, session_factory, file_watcher: FileWatcherService, config_service: ConfigService, notification_service: "NotificationService"):
        self.session_factory = session_factory
        self.file_watcher = file_watcher
        self.notification_service = notification_service
        self.web_scraper = WebScraper()
        self.article_parser = ArticleParser()
        
        # Инициализация подсистем
        self.api_risk_checker = ApiRiskCheckerService(session_factory)
        self.telegram_monitor = TelegramMonitorService(session_factory, self.article_parser, config_service)
        self.blog_scraper = BlogScraperService(session_factory, self.web_scraper, self.article_parser)
    
    async def _update_pair_risk(self, session, pair: MonitoredPair, new_risk: RiskLevel, 
                                 signal_type: SignalType, msg: str, evidence: DelistingEvent = None) -> bool:
        """
        Универсальный метод обновления риска пары и отправки сигналов.
        Обновляет статус если приоритет вырос, и всегда ищет дубликаты перед созданием сигнала.
        """
        changed = False

        # Разделяем логику на проверки
        is_upgraded = new_risk.priority > pair.risk_level.priority
        is_same_level = new_risk.priority == pair.risk_level.priority

        # 1. Обновляем уровень риска, ТОЛЬКО если он реально повысился
        if is_upgraded:
            pair.risk_level = new_risk
            session.add(pair)
            changed = True
            
        # 2. Генерируем сигнал при ПОВЫШЕНИИ риска ИЛИ при новом событии того же уровня
        if (is_upgraded or is_same_level) and new_risk != RiskLevel.NORMAL:
            # Условия для поиска дублей
            conditions = [
                Signal.type == signal_type, 
                Signal.pair_id == pair.id
            ]
            
            # Если передан эвиденс (событие), ищем упоминание конкретной биржи
            if evidence:
                conditions.append(Signal.raw_message.like(f"%Info from: {evidence.exchange}.%"))
                # Если это реальная статья (не API тег), ищем конкретный URL статьи, чтобы не пропустить новые статьи
                if evidence.announcement_url and evidence.announcement_url != "API" and not evidence.announcement_url.startswith("http://API"):
                    conditions.append(Signal.raw_message.like(f"%Article: {evidence.announcement_url}%"))
            
            sig_check = select(Signal).where(*conditions)
            existing_sig = (await session.execute(sig_check)).first()
            
            if not existing_sig:
                logger.warning(f"Creating NEW signal (risk event detected): {msg}")
                new_sig = Signal(type=signal_type, pair_id=pair.id, raw_message=msg)
                session.add(new_sig)
                await session.commit()
                await session.refresh(new_sig)
                
                # Отправка через инжектированный сервис уведомлений
                asyncio.create_task(self.notification_service.send_and_log_signal(new_sig.id, msg, prefix=""))
                changed = True  # Сигнал создан - значит система изменилась
            else:
                logger.info(f"Signal already exists, skipping: {msg[:100]}...")

        return changed

    async def demote_orphaned_risks(self, session: AsyncSession):
        """
        Проверяет пары с повышенным риском и 'ступенчато' сбрасывает его,
        если соответствующих событий больше нет.
        Это исправляет ситуацию, когда DelistingEvent удаляется вручную из БД (полностью или частично).
        """
        logger.info("Проверка и сброс 'осиротевших' рисков...")
        
        # 1. Находим все пары с риском выше нормы
        risky_pairs_stmt = select(MonitoredPair).where(MonitoredPair.risk_level != RiskLevel.NORMAL)
        risky_pairs = (await session.execute(risky_pairs_stmt)).scalars().all()

        if not risky_pairs:
            logger.info("Пар с повышенным риском не найдено.")
            return

        # 2. Собираем их базовые валюты и все связанные с ними события
        base_symbols = list({p.base_currency for p in risky_pairs})
        events_stmt = select(DelistingEvent).where(DelistingEvent.symbol.in_(base_symbols))
        all_events = (await session.execute(events_stmt)).scalars().all()

        demoted_count = 0
        
        # 3. Пересчитываем реальный (оправданный) риск для каждой пары
        for pair in risky_pairs:
            base_currency = pair.base_currency
            
            # Ивенты ТОЛЬКО для этой базовой валюты
            pair_events = [e for e in all_events if e.symbol == base_currency]
            
            if not pair_events:
                # Если ивентов нет совсем - полный сброс до NORMAL
                logger.warning(f"Полный сброс риска для {pair.symbol}: нет активных событий. "
                               f"Был риск: {pair.risk_level.name}")
                pair.risk_level = RiskLevel.NORMAL
                session.add(pair)
                demoted_count += 1
                
                # Функция UI уже удаляет нужные сигналы при очистке, 
                # но оставляем этот блок как "сборщик мусора" для гарантии очистки БД
                await session.execute(delete(Signal).where(
                    Signal.pair_id == pair.id,
                    Signal.type.in_([SignalType.DELISTING_WARNING, SignalType.ST_WARNING])
                ))
            else:
                # Ивенты есть. Вычисляем максимально оправданный риск из того, что осталось.
                theoretical_max_risk = RiskLevel.NORMAL
                
                for ev in pair_events:
                    is_direct = (ev.exchange.upper() == pair.exchange.upper())
                    ev_risk = RiskLevel.NORMAL
                    
                    if ev.type == DelistingEventType.DELISTING:
                        ev_risk = RiskLevel.DELISTING_PLANNED if is_direct else RiskLevel.CROSS_DELISTING
                    elif ev.type == DelistingEventType.ST:
                        ev_risk = RiskLevel.RISK_ZONE if is_direct else RiskLevel.CROSS_RISK
                        
                    if ev_risk.priority > theoretical_max_risk.priority:
                        theoretical_max_risk = ev_risk
                
                # Если посчитанный риск ОКРУЖЕНИЯ ниже текущего риска ПАРЫ -> понижаем его!
                if theoretical_max_risk.priority < pair.risk_level.priority:
                    logger.info(f"Ступенчатое понижение риска для {pair.symbol}: {pair.risk_level.name} -> {theoretical_max_risk.name}")
                    pair.risk_level = theoretical_max_risk
                    session.add(pair)
                    demoted_count += 1

        if demoted_count > 0:
            await session.commit()
            logger.success(f"Понижен/сброшен риск для {demoted_count} пар.")
        else:
            logger.info("'Осиротевших' рисков не найдено.")

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
        bases = list({p.base_currency for p in active_pairs})
        
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
            base_currency = pair.base_currency
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
                    if await self._update_pair_risk(session, pair, new_risk, signal_type, msg, evidence):
                        pairs_updated += 1

        if pairs_updated > 0:
            await session.commit()
            logger.warning(f"Матчинг завершен: обновлено {pairs_updated} пар!")
        else:
            logger.info("Матчинг завершен: изменений не найдено.")
            
        return pairs_updated

    async def check_all_risks(self):
        """
        Вызывает все проверки риска: Telegram, блоги и API.
        Перед проверкой автоматически синхронизирует список пар из файлов.
        """
        logger.info("=== Запуск полной проверки рисков Delistings + ST ===")
        
        try:
            # 0. Синхронизация — используем существующий синглтон
            logger.info("Синхронизация списка пар из файлов...")
            stats = await self.file_watcher.sync_from_settings()
            logger.info(f"Синхронизация завершена: {stats}")
            
            async with get_session() as session:
                # Сначала сбрасываем риски, для которых больше нет событий
                await self.demote_orphaned_risks(session)

        except Exception as e:
            logger.error(f"Ошибка на этапе синхронизации и очистки: {e}")
        
        # 1. Telegram
        try:
            await self.telegram_monitor.check_binance_telegram_channel()
        except Exception as e:
            logger.error(f"Ошибка при проверке Telegram: {e}")

        # 2. Web Scraping (Blogs)
        try:
            await self.blog_scraper.check_delistings_blog()
        except Exception as e:
            logger.error(f"Ошибка при проверке блогов: {e}")
        
        # 3. API Checks
        try:
            await self.api_risk_checker.check_api_risks()
        except Exception as e:
            logger.error(f"Ошибка при проверке API: {e}")

        # 4. Единый матчинг — один раз после всех проверок
        try:
            async with get_session() as session:
                matches = await self.match_monitored_pairs_with_events(session)
                if matches > 0:
                    logger.info(f"Матчинг: обновлено {matches} пар")
        except Exception as e:
            logger.error(f"Ошибка при матчинге пар с событиями: {e}")

        logger.info("=== Полная проверка рисков Delistings + ST завершена ===")
