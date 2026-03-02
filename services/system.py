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

    # 0. Config Service (фундамент — от него зависят остальные)
    services.config = ConfigService(get_session)

    # 1. Alert Engine
    services.alert_engine = AlertEngine(get_session)

    # 2. Market Data Service (получает AlertEngine через DI)
    services.market = MarketDataService(get_session, services.alert_engine)

    # 3. Scraper Service
    services.scraper = ScraperService(get_session)

    # 4. CMC Service
    from services.cmc import CMCService
    services.cmc = CMCService(get_session)

    # 5. Scheduler Service (нужен market, scraper и cmc)
    services.scheduler = SchedulerService(services.market, services.scraper, services.cmc)

    # 6. File Watcher Service
    from services.file_watcher import FileWatcherService
    services.file_watcher = FileWatcherService(get_session)

    # 7. Telegram Service + загрузка настроек из БД
    tg_conf = await services.config.get_telegram_config()
    services.telegram = TelegramService(token=tg_conf.bot_token, chat_id=tg_conf.chat_id)

    if tg_conf.bot_token and tg_conf.chat_id:
        logger.info("Telegram Service инициализирован настройками из БД.")
