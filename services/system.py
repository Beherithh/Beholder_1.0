from loguru import logger # Перемещено в начало файла

from database.core import get_session
from services.market_data import MarketDataService
from services.scheduler import SchedulerService
from services.scraper import ScraperService
from services.telegram import TelegramService
from services.config import ConfigService
from services.alert_engine import AlertEngine

# Глобальные переменные для синглтонов
market_service: MarketDataService = None
scraper_service: ScraperService = None
scheduler_service: SchedulerService = None
telegram_service: TelegramService = None
file_watcher_service: "FileWatcherService" = None
config_service: ConfigService = None
alert_engine: AlertEngine = None
cmc_service: "CMCService" = None

async def init_services():
    """
    Инициализирует сервисы. Должна вызываться один раз при старте приложения.
    """
    global market_service, scraper_service, scheduler_service, telegram_service, file_watcher_service, config_service, alert_engine, cmc_service
    
    # 0. Config Service (Фундамент)
    config_service = ConfigService(get_session)

    # 1. Alert Engine
    alert_engine = AlertEngine(get_session)

    # 2. Market Data Service
    market_service = MarketDataService(get_session)
    
    # 3. Scraper Service
    scraper_service = ScraperService(get_session)
    
    # 4. CMC Service
    from services.cmc import CMCService
    cmc_service = CMCService(get_session)

    # 5. Scheduler Service (нужен market_service, scraper_service и cmc_service)
    scheduler_service = SchedulerService(market_service, scraper_service, cmc_service)

    # 6. File Watcher Service
    from services.file_watcher import FileWatcherService
    file_watcher_service = FileWatcherService(get_session)

    # 7. Telegram Service + Загрузка настроек через ConfigService
    tg_conf = await config_service.get_telegram_config()
    telegram_service = TelegramService(token=tg_conf.bot_token, chat_id=tg_conf.chat_id)
    
    if tg_conf.bot_token and tg_conf.chat_id:
        logger.info("Telegram Service инициализирован настройками из БД.")

def get_scheduler() -> SchedulerService:
    if not scheduler_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return scheduler_service

def get_cmc_service():
    if not cmc_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return cmc_service

def get_market_service() -> MarketDataService:
    if not market_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return market_service

def get_scraper_service() -> ScraperService:
    if not scraper_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return scraper_service

def get_telegram_service() -> TelegramService:
    if not telegram_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return telegram_service

def get_file_watcher_service() -> "FileWatcherService":
    if not file_watcher_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return file_watcher_service

def get_config_service() -> ConfigService:
    if not config_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return config_service

def get_alert_engine() -> AlertEngine:
    if not alert_engine:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return alert_engine
