from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from sqlmodel import select

from database.models import AppSettings # AppSettings все еще нужен для _ensure_default_settings в database.core
from database.core import get_session
from services.market_data import MarketDataService
from services.scraper import ScraperService
from services.cmc import CMCService
from services.config import ConfigService
from services.alert_engine import AlertEngine

class SchedulerService:
    """
    Сервис-планировщик для запуска фоновых задач по расписанию.
    """
    def __init__(self, market_data_service: MarketDataService, scraper_service: ScraperService, cmc_service: CMCService, alert_engine: AlertEngine, config_service: ConfigService):
        self.scheduler = AsyncIOScheduler()
        self.market_service = market_data_service
        self.scraper_service = scraper_service
        self.cmc_service = cmc_service
        self.alert_engine = alert_engine
        self.config_service = config_service
        
        self.job_id_market = "market_data_update"
        self.job_id_scraper = "scraper_check"
        self.job_id_cmc = "cmc_rank_update"

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler запущен.")

    async def schedule_all(self):
        """
        Запускает все периодические задачи на основе настроек из ConfigService.
        """
        config = await self.config_service.get_scheduler_config()

        await self.schedule_market_update(config.market_update_interval_hours)
        await self.schedule_scraper_check(config.scraper_interval_hours)
        await self.schedule_cmc_update(config.cmc_update_interval_days)

    def _schedule_job(self, job_id, func, interval_hours, minute_val, log_name):
        """Универсальный метод для планирования задачи."""
        # Удаляем старую задачу, если она есть
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            
        # APScheduler: 24h = раз в день, */N = каждые N часов, * = каждый час
        cron_hour = '*' if interval_hours == 1 else ('0' if interval_hours == 24 else f"*/{interval_hours}")
            
        self.scheduler.add_job(
            func,
            trigger=CronTrigger(hour=cron_hour, minute=str(minute_val)),
            id=job_id,
            replace_existing=True
        )
        logger.info(f"{log_name}: Каждые {interval_hours} ч. (в {minute_val} мин)")

    async def schedule_scraper_check(self, interval_hours: int):
        """Планирование задачи скрапера."""
        if not (1 <= interval_hours <= 24):
            logger.warning(f"Некорректный интервал скрапера: {interval_hours}. Использую дефолт 1 час.")
            interval_hours = 1
        self._schedule_job(
            job_id=self.job_id_scraper,
            func=self.scraper_service.check_all_risks,
            interval_hours=interval_hours,
            minute_val=15, # :15 для скрапера
            log_name="Планирование скрапера"
        )

    async def schedule_market_update(self, interval_hours: int):
        """Планирование задачи обновления свечей."""
        if not (1 <= interval_hours <= 24):
            logger.warning(f"Некорректный интервал обновления рынка: {interval_hours}. Использую дефолт 1 час.")
            interval_hours = 1
        self._schedule_job(
            job_id=self.job_id_market,
            func=self.run_market_cycle,
            interval_hours=interval_hours,
            minute_val=5, # :05 для свечей
            log_name="Планирование обновления рынка"
        )

    async def run_market_cycle(self):
        """Оркестратор полного цикла: скачивание свечей -> сбор курсов -> расчет алертов."""
        try:
            logger.info("Scheduler: Запуск цикла Market Data...")
            await self.market_service.update_all()
            
            # Получаем курсы и конфиг
            rates = await self.market_service.get_quote_rates()
            config = await self.config_service.get_alert_config()
            
            # Запускаем анализ
            await self.alert_engine.analyze_all(config, rates)
            logger.info("Scheduler: Цикл Market Data завершен.")
        except Exception as e:
            logger.error(f"Ошибка в run_market_cycle (Scheduler): {e}")

    async def schedule_cmc_update(self, interval_days: int):
        """Планирование обновления рангов CMC (интервал в днях)."""
        if not (1 <= interval_days <= 30):
            logger.warning(f"Некорректный интервал CMC: {interval_days}. Использую дефолт 5 дней.")
            interval_days = 5

        if self.scheduler.get_job(self.job_id_cmc):
            self.scheduler.remove_job(self.job_id_cmc)

        # Запускаем раз в N дней в 04:30
        self.scheduler.add_job(
            self.cmc_service.sync_ranks,
            trigger=CronTrigger(day=f"*/{interval_days}", hour="4", minute="30"),
            id=self.job_id_cmc,
            replace_existing=True
        )
        logger.info(f"Планирование CMC: Каждые {interval_days} дн. (в 04:30)")

    async def update_cmc_interval(self, new_days: int):
        """Метод для UI: перепланирует задачу CMC."""
        await self.schedule_cmc_update(new_days)

    async def update_market_interval(self, new_hours: int):
        """Метод для UI: перепланирует задачу обновления свечей."""
        await self.schedule_market_update(new_hours)

    async def update_scraper_interval(self, new_hours: int):
        """Метод для UI: перепланирует задачу скрапера."""
        await self.schedule_scraper_check(new_hours)
