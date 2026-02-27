import asyncio
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
from database.core import get_session

class ScraperService:
    """
    Сервис-координатор для мониторинга внешних источников.
    Объединяет работу:
    - TelegramMonitorService (Telegram)
    - BlogScraperService (Блоги бирж)
    - ApiRiskCheckerService (API бирж)
    """

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.web_scraper = WebScraper()
        self.article_parser = ArticleParser()
        
        # Инициализация подсистем
        self.api_risk_checker = ApiRiskCheckerService(session_factory)
        self.telegram_monitor = TelegramMonitorService(session_factory, self.article_parser)
        self.blog_scraper = BlogScraperService(session_factory, self.web_scraper, self.article_parser)
    
    async def _update_pair_risk(self, session, pair: MonitoredPair, new_risk: RiskLevel, 
                                 signal_type: SignalType, msg: str, evidence: DelistingEvent = None) -> bool:
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
                    logger.warning(f"Creating NEW signal (risk increased): {msg}")
                    new_sig = Signal(type=signal_type, pair_id=pair.id, raw_message=msg)
                    session.add(new_sig)
                    await session.commit()
                    await session.refresh(new_sig)
                    
                    # Отправка в Telegram
                    from services.notifications import send_and_log_signal
                    asyncio.create_task(send_and_log_signal(new_sig.id, msg, prefix=""))
                else:
                    logger.info(f"Signal already exists, skipping: {msg[:100]}...")

        return changed

    async def _demote_orphaned_risks(self, session: AsyncSession):
        """
        Проверяет пары с повышенным риском и сбрасывает его, если нет активных событий.
        Это исправляет ситуацию, когда DelistingEvent удаляется вручную из БД.
        """
        logger.info("Проверка и сброс 'осиротевших' рисков...")
        
        # 1. Находим все пары с риском выше нормы
        risky_pairs_stmt = select(MonitoredPair).where(MonitoredPair.risk_level != RiskLevel.NORMAL)
        risky_pairs = (await session.execute(risky_pairs_stmt)).scalars().all()

        if not risky_pairs:
            logger.info("Пар с повышенным риском не найдено.")
            return

        # 2. Собираем их базовые валюты и все связанные с ними события
        base_symbols = list({p.symbol.split('/')[0] for p in risky_pairs})
        events_stmt = select(DelistingEvent).where(DelistingEvent.symbol.in_(base_symbols))
        all_events = (await session.execute(events_stmt)).scalars().all()
        
        # Создаем множество символов, у которых ДЕЙСТВИТЕЛЬНО есть события
        symbols_with_events = {event.symbol for event in all_events}

        demoted_count = 0
        # 3. Ищем "сирот"
        for pair in risky_pairs:
            base_currency = pair.symbol.split('/')[0]
            if base_currency not in symbols_with_events:
                logger.warning(f"Сброс риска для {pair.symbol}: нет активных событий Delisting/ST. "
                               f"Текущий риск: {pair.risk_level.name}")
                
                # Сбрасываем риск
                pair.risk_level = RiskLevel.NORMAL
                session.add(pair)
                demoted_count += 1
                
                # Удаляем старые сигналы, связанные с этим
                await session.execute(delete(Signal).where(
                    Signal.pair_id == pair.id,
                    Signal.type.in_([SignalType.DELISTING_WARNING, SignalType.ST_WARNING])
                ))

        if demoted_count > 0:
            await session.commit()
            logger.success(f"Сброшен риск для {demoted_count} пар.")
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
        Вызывает все проверки риска: блог и API.
        Перед проверкой автоматически синхронизирует список пар из файлов.
        """
        logger.info("=== Запуск полной проверки рисков Delistings + ST ===")
        
        try:
            # 0. Синхронизация и очистка
            logger.info("Синхронизация списка пар из файлов...")
            from services.file_watcher import FileWatcherService
            watcher = FileWatcherService(get_session)
            stats = await watcher.sync_from_settings()
            logger.info(f"Синхронизация завершена: {stats}")
            
            async with get_session() as session:
                # Сначала сбрасываем риски, для которых больше нет событий
                await self._demote_orphaned_risks(session)
                # Затем матчим существующие пары с существующими событиями
                matches = await self.match_monitored_pairs_with_events(session)
                logger.info(f"Найдено совпадений с историей: {matches}")

        except Exception as e:
            logger.error(f"Ошибка на этапе синхронизации и очистки: {e}")
        
        # 1. Telegram
        tg_events = await self.telegram_monitor.check_binance_telegram_channel()
        if tg_events > 0:
            async with get_session() as session:
                await self.match_monitored_pairs_with_events(session)

        # 2. Web Scraping (Blogs)
        blog_events = await self.blog_scraper.check_delistings_blog()
        if blog_events > 0:
            async with get_session() as session:
                await self.match_monitored_pairs_with_events(session)
        
        # 3. API Checks
        api_changes = await self.api_risk_checker.check_api_risks()
        if api_changes:
             async with get_session() as session:
                await self.match_monitored_pairs_with_events(session)

        logger.info("=== Полная проверка рисков Delistings + ST завершена ===")
