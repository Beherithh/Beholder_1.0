from database.core import get_session
from services.market_data import MarketDataService
from services.scheduler import SchedulerService
from services.scraper import ScraperService
from services.telegram import TelegramService

# Глобальные переменные для синглтонов
market_service: MarketDataService = None
scraper_service: ScraperService = None
scheduler_service: SchedulerService = None
telegram_service: TelegramService = None
file_watcher_service: "FileWatcherService" = None

async def init_services():
    """
    Инициализирует сервисы. Должна вызываться один раз при старте приложения.
    """
    global market_service, scraper_service, scheduler_service, telegram_service, file_watcher_service
    
    # 1. Market Data Service
    market_service = MarketDataService(get_session)
    
    # 2. Scraper Service
    scraper_service = ScraperService(get_session)
    
    # 3. CMC Service
    from services.cmc import CMCService
    cmc_service = CMCService(get_session)

    # 4. Scheduler Service (нужен market_service, scraper_service и cmc_service)
    scheduler_service = SchedulerService(market_service, scraper_service, cmc_service)

    # 5. File Watcher Service
    from services.file_watcher import FileWatcherService
    file_watcher_service = FileWatcherService(get_session)

    # 4. Telegram Service + Загрузка настроек из БД
    from database.models import AppSettings
    async with get_session() as session:
        token_set = await session.get(AppSettings, "tg_bot_token")
        chat_id_set = await session.get(AppSettings, "tg_chat_id")
        
        token = token_set.value if token_set else None
        chat_id = chat_id_set.value if chat_id_set else None
        
        telegram_service = TelegramService(token=token, chat_id=chat_id)
        if token and chat_id:
            from loguru import logger
            logger.info("Telegram Service инициализирован настройками из БД.")

def get_scheduler() -> SchedulerService:
    if not scheduler_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return scheduler_service

def get_cmc_service():
    from services.cmc import CMCService
    return CMCService(get_session)

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
