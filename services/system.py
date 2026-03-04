"""
Центральный реестр сервисов приложения.

Вместо 8 глобальных переменных и 8 одинаковых геттеров
используется единый типизированный контейнер ServiceContainer.

Использование:
    from services.system import services
    services.telegram.send_message(...)
    services.config.get_alert_config()
"""
from __future__ import annotations

from loguru import logger

from database.core import get_session
from services.market_data import MarketDataService
from services.scheduler import SchedulerService
from services.scraper import ScraperService
from services.telegram import TelegramService
from services.config import ConfigService
from services.alert_engine import AlertEngine
from services.notifications import NotificationService


class ServiceContainer:
    """Центральный реестр сервисов (Singleton).

    Все сервисы доступны как типизированные атрибуты:
        services.config, services.telegram, services.scheduler и т.д.

    Атрибуты заполняются в init_services() при старте приложения.
    """

    _instance: ServiceContainer | None = None

    def __init__(self) -> None:
        # Каждый атрибут — None до вызова init_services()
        self.config: ConfigService | None = None
        self.alert_engine: AlertEngine | None = None
        self.notifications: NotificationService | None = None
        self.market: MarketDataService | None = None
        self.scraper: ScraperService | None = None
        self.scheduler: SchedulerService | None = None
        self.telegram: TelegramService | None = None
        self.file_watcher: "FileWatcherService | None" = None
        self.cmc: "CMCService | None" = None

    @classmethod
    def instance(cls) -> ServiceContainer:
        """Возвращает единственный экземпляр контейнера (lazy singleton)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# Модульная переменная — единая точка доступа ко всем сервисам
services = ServiceContainer.instance()


async def init_services() -> None:
    """Инициализирует все сервисы. Вызывается один раз при старте приложения."""

    # 0. Config Service (фундамент — от него зависят все остальные)
    services.config = ConfigService(get_session)

    # 1. Telegram Service — инициализируем рано, т.к. другие сервисы могут посылать алерты
    tg_conf = await services.config.get_telegram_config()
    services.telegram = TelegramService(token=tg_conf.bot_token, chat_id=tg_conf.chat_id)

    if tg_conf.bot_token and tg_conf.chat_id:
        logger.info("Telegram Service инициализирован настройками из БД.")
    else:
        logger.warning("Telegram Service инициализирован без credentials (не настроен в БД).")

    # 2. Notification Service (получает TelegramService через DI — нет global-состояния)
    services.notifications = NotificationService(services.telegram, get_session)

    # 3. Alert Engine (получает notification_service через DI)
    services.alert_engine = AlertEngine(get_session, services.notifications)

    # 4. File Watcher Service (нужен scraper-у)
    from services.file_watcher import FileWatcherService
    services.file_watcher = FileWatcherService(get_session, config_service=services.config)

    # 5. Scraper Service (получает file_watcher, config, notifications через DI)
    services.scraper = ScraperService(
        get_session,
        file_watcher=services.file_watcher,
        config_service=services.config,
        notification_service=services.notifications,
    )

    # 6. Market Data Service (получает только config через DI)
    services.market = MarketDataService(get_session, config_service=services.config)

    # 7. CMC Service (получает config и notifications через DI)
    from services.cmc import CMCService
    services.cmc = CMCService(get_session, config_service=services.config, notification_service=services.notifications)

    # 8. Scheduler Service (нужен market, scraper, cmc, alert_engine и config)
    services.scheduler = SchedulerService(services.market, services.scraper, services.cmc, services.alert_engine, config_service=services.config)

