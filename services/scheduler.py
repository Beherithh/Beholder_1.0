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

    async def schedule_scraper_check(self):
        """
        Запуск скрапера раз в час (или конфигурируемо).
        Пока жестко раз в час на 15-й минуте.
        """
        # Удаляем старую задачу если есть
        if self.scheduler.get_job(self.job_id_scraper):
            self.scheduler.remove_job(self.job_id_scraper)
            
        self.scheduler.add_job(
            self.scraper_service.check_delistings_blog,
            trigger=CronTrigger(minute="15"), # Каждый час в XX:15
            id=self.job_id_scraper,
            replace_existing=True
        )
        logger.info("Планирование скрапера: Каждый час в 15 минут.")

    async def schedule_market_update(self):
        """
        Читает настройки из БД и (пере)запускает задачу обновления свечей.
        """
        interval = self.default_interval_hours
        
        # Пытаемся получить настройки пользователя
        async with get_session() as session:
            settings_obj = await session.get(AppSettings, "update_interval_hours")
            if settings_obj:
                try:
                    val = int(settings_obj.value)
                    # Ограничение 1-24 часа
                    if 1 <= val <= 24:
                        interval = val
                except ValueError:
                    pass
        
        # Формируем Cron trigger: 
        # "Каждый X час, на 2-й минуте".
        # hour='*/interval'
        
        logger.info(f"Планирование обновления рынка: Каждые {interval} часов в {self.start_at_minute} минут.")
        
        # Удаляем старую задачу если есть
        if self.scheduler.get_job(self.job_id_market):
            self.scheduler.remove_job(self.job_id_market)
            
        self.scheduler.add_job(
            self.market_service.update_all,
            trigger=CronTrigger(hour=f"*/{interval}", minute=str(self.start_at_minute)),
            id=self.job_id_market,
            replace_existing=True
        )

    async def update_interval(self, new_hours: int):
        """
        Метод для вызова из UI при смене настроек.
        Сохраняет в БД и обновляет задачу.
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
