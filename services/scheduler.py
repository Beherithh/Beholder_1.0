from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
import json
from sqlmodel import select

from database.models import AppSettings
from database.core import get_session
from services.market_data import MarketDataService
from services.scraper import ScraperService

class SchedulerService:
    """
    Сервис-планировщик для запуска фоновых задач по расписанию.
    """
    
    def __init__(self, market_data_service: MarketDataService, scraper_service: ScraperService):
        self.scheduler = AsyncIOScheduler()
        self.market_service = market_data_service
        self.scraper_service = scraper_service
        
        self.job_id_market = "market_data_update"
        self.job_id_scraper = "scraper_check"
        
        # Значения по умолчанию
        self.default_interval_hours = 1
        self.start_at_minute = 2 

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler запущен.")

    async def schedule_all(self):
        """
        Запускает все периодические задачи.
        """
        await self.schedule_market_update()
        await self.schedule_scraper_check()

    async def _schedule_job_from_settings(self, job_id, func, setting_key, default_interval, minute_val, log_name):
        """
        Универсальный метод для планирования задачи на основе настройки из БД.
        """
        interval = default_interval
        
        async with get_session() as session:
            settings_obj = await session.get(AppSettings, setting_key)
            if settings_obj:
                try:
                    val = int(settings_obj.value)
                    if 1 <= val <= 24:
                        interval = val
                except ValueError:
                    pass

        # Удаляем старую задачу если есть
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            
        # Fix for APScheduler: Handle 24h interval (once a day) vs hourly
        cron_hour = '*' if interval == 1 else ('0' if interval == 24 else f"*/{interval}")
            
        self.scheduler.add_job(
            func,
            trigger=CronTrigger(hour=cron_hour, minute=str(minute_val)),
            id=job_id,
            replace_existing=True
        )
        logger.info(f"{log_name}: Каждые {interval} ч. (в {minute_val} мин)")

    async def schedule_scraper_check(self):
        """
        Запуск скрапера раз в час (или конфигурируемо).
        """
        await self._schedule_job_from_settings(
            job_id=self.job_id_scraper,
            func=self.scraper_service.check_delistings_blog,
            setting_key="scraper_interval_hours",
            default_interval=1,
            minute_val=self.start_at_minute + 10, # +10 минут чтобы не конфликтовало с загрузкой OHLC
            log_name="Планирование скрапера"
        )

    async def schedule_market_update(self):
        """
        Читает настройки из БД и (пере)запускает задачу обновления свечей.
        """
        await self._schedule_job_from_settings(
            job_id=self.job_id_market,
            func=self.market_service.update_all,
            setting_key="update_interval_hours",
            default_interval=self.default_interval_hours,
            minute_val=self.start_at_minute,
            log_name="Планирование обновления рынка"
        )

    async def update_interval(self, new_hours: int):
        """
        Метод для вызова из UI при смене настроек (Market Data).
        """
        if not (1 <= new_hours <= 24):
            logger.warning(f"Некорректный интервал: {new_hours}")
            return

        async with get_session() as session:
            settings = await session.get(AppSettings, "update_interval_hours")
            if not settings:
                settings = AppSettings(key="update_interval_hours", value=str(new_hours))
                session.add(settings)
            else:
                settings.value = str(new_hours)
            await session.commit()
            
        await self.schedule_market_update()

    async def update_scraper_interval(self, new_hours: int):
        """
        Метод для вызова из UI при смене настроек (Scraper).
        """
        if not (1 <= new_hours <= 24):
            logger.warning(f"Некорректный интервал скрапера: {new_hours}")
            return

        async with get_session() as session:
            settings = await session.get(AppSettings, "scraper_interval_hours")
            if not settings:
                settings = AppSettings(key="scraper_interval_hours", value=str(new_hours))
                session.add(settings)
            else:
                settings.value = str(new_hours)
            await session.commit()
            
        await self.schedule_scraper_check()
