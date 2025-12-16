from database.core import get_session
from services.market_data import MarketDataService
from services.scheduler import SchedulerService
from services.scraper import ScraperService

# Глобальные переменные для синглтонов
market_service: MarketDataService = None
scraper_service: ScraperService = None
scheduler_service: SchedulerService = None

def init_services():
    """
    Инициализирует сервисы. Должна вызываться один раз при старте приложения.
    """
    global market_service, scraper_service, scheduler_service
    
    # 1. Market Data Service
    market_service = MarketDataService(get_session)
    
    # 2. Scraper Service
    scraper_service = ScraperService(get_session)
    
    # 3. Scheduler Service (нужен market_service и scraper_service)
    scheduler_service = SchedulerService(market_service, scraper_service)

def get_scheduler() -> SchedulerService:
    if not scheduler_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return scheduler_service

def get_market_service() -> MarketDataService:
    if not market_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return market_service

def get_scraper_service() -> ScraperService:
    if not scraper_service:
        raise RuntimeError("Services not initialized! Call init_services() first.")
    return scraper_service
