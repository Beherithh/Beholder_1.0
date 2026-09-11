"""
Архитектурный компонентный тест контейнера сервисов (Component / Wiring Test).

Проверяет:
1. Корректность работы init_services() в services.system.
2. Отсутствие ошибок внедрения зависимостей (Dependency Injection):
   - Все 9 сервисов приложения успешно инициализируются.
   - Граф зависимостей собран верно (NotificationService ссылается на TelegramService,
     AlertEngine ссылается на NotificationService и т.д.).
3. Гарантия защиты от регрессий: если в будущем разработчик добавит/удалит аргумент
   в конструкторе любого сервиса и забудет обновить init_services(), этот тест мгновенно упадёт.
"""

import pytest
from unittest.mock import patch, AsyncMock

from services.system import services, init_services, ServiceContainer
from services.config import ConfigService, TelegramConfig
from services.telegram import TelegramService
from services.notifications import NotificationService
from services.alert_engine import AlertEngine
from services.file_watcher import FileWatcherService
from services.scraper import ScraperService
from services.market_data import MarketDataService
from services.cmc import CMCService
from services.scheduler import SchedulerService


@pytest.mark.asyncio
class TestServiceContainerWiring:
    """Проверка сборки контейнера зависимостей всего приложения."""

    async def test_init_services_wires_all_dependencies_correctly(self) -> None:
        """
        Проверяет, что init_services() полностью инициализирует ServiceContainer
        и связывает компоненты между собой по правилам DI.
        """
        # Мокаем чтение TelegramConfig из БД, чтобы не было внешних сетевых вызовов
        mock_tg_conf = TelegramConfig(bot_token="test_token", chat_id="123456")

        with patch.object(ConfigService, "get_telegram_config", new_callable=AsyncMock) as mock_get_tg:
            mock_get_tg.return_value = mock_tg_conf

            # Запускаем инициализацию всех сервисов ядра Beholder
            await init_services()

        # 1. Проверяем, что ни один сервис не остался None
        assert isinstance(services.config, ConfigService)
        assert isinstance(services.telegram, TelegramService)
        assert isinstance(services.notifications, NotificationService)
        assert isinstance(services.alert_engine, AlertEngine)
        assert isinstance(services.file_watcher, FileWatcherService)
        assert isinstance(services.scraper, ScraperService)
        assert isinstance(services.market, MarketDataService)
        assert isinstance(services.cmc, CMCService)
        assert isinstance(services.scheduler, SchedulerService)

        # 2. Проверяем корректность связей в графе зависимостей (Wiring integrity)
        assert services.notifications.telegram is services.telegram
        assert services.alert_engine.notification_service is services.notifications
        assert services.scraper.file_watcher is services.file_watcher
        assert services.scraper.notification_service is services.notifications
        assert services.scraper.telegram_monitor.config_service is services.config
        assert services.market.config_service is services.config
        assert services.cmc.notification_service is services.notifications
        assert services.scheduler.alert_engine is services.alert_engine
        assert services.scheduler.market_service is services.market
        assert services.scheduler.scraper_service is services.scraper
        assert services.scheduler.cmc_service is services.cmc
        assert services.scheduler.config_service is services.config
