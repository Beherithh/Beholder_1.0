import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.scraper import ScraperService
from database.models import MonitoredPair, RiskLevel, SignalType

class TestScraperService:
    
    @pytest.fixture
    def mock_session_factory(self):
        """Фикстура для создания мока сессии БД"""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()  # session.add is synchronous
        mock_factory = MagicMock(return_value=mock_session)
        # Нужно, чтобы контекстный менеджер (async with factory()) возвращал сессию
        mock_factory.__aenter__.return_value = mock_session
        return mock_factory

    @pytest.fixture
    def scraper_service(self, mock_session_factory):
        """Фикстура для создания сервиса с замоканными зависимостями"""
        # Патчим создание подсистем, чтобы они не лезли в сеть
        with patch('services.scraper.WebScraper') as MockWebScraper, \
             patch('services.scraper.ArticleParser') as MockArticleParser, \
             patch('services.scraper.ApiRiskCheckerService') as MockApiRiskChecker, \
             patch('services.scraper.TelegramMonitorService') as MockTelegramMonitor, \
             patch('services.scraper.BlogScraperService') as MockBlogScraper:
            
            service = ScraperService(mock_session_factory)
            
            # Настраиваем моки подсистем
            service.api_risk_checker.check_api_risks = AsyncMock(return_value=False)
            service.telegram_monitor.check_binance_telegram_channel = AsyncMock(return_value=0)
            service.blog_scraper.check_delistings_blog = AsyncMock(return_value=0)
            
            # Мокаем метод матчинга, так как он сложный и требует БД
            service.match_monitored_pairs_with_events = AsyncMock(return_value=0)
            
            return service

    @pytest.mark.asyncio
    async def test_check_all_risks_calls_subsystems(self, scraper_service):
        """Проверка, что check_all_risks вызывает все подсистемы"""
        
        # Мокаем demote_orphaned_risks, чтобы он не лез в БД
        scraper_service.demote_orphaned_risks = AsyncMock()

        # Мок для get_session (используется внутри check_all_risks для demote и match)
        mock_session = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        # Патчим services.file_watcher (синглтон) и get_session
        mock_watcher = AsyncMock()
        mock_watcher.sync_from_settings = AsyncMock(return_value="Synced")

        with patch('services.scraper.get_session', return_value=mock_session_ctx), \
             patch('services.system.services.file_watcher', mock_watcher):
            
            await scraper_service.check_all_risks()
            
            # Проверяем вызовы подсистем
            scraper_service.telegram_monitor.check_binance_telegram_channel.assert_called_once()
            scraper_service.blog_scraper.check_delistings_blog.assert_called_once()
            scraper_service.api_risk_checker.check_api_risks.assert_called_once()
            
            # Проверяем, что demote_orphaned_risks вызвался
            scraper_service.demote_orphaned_risks.assert_called_once()

            # Проверяем, что матчинг вызвался один раз (в конце)
            scraper_service.match_monitored_pairs_with_events.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_pair_risk_logic(self, scraper_service, mock_session_factory):
        """Проверка логики обновления риска (повышение vs понижение)"""
        session = mock_session_factory()
        
        # Создаем тестовую пару
        pair = MonitoredPair(symbol="BTC/USDT", risk_level=RiskLevel.NORMAL)
        
        # 1. Попытка повысить риск (NORMAL -> RISK_ZONE)
        changed = await scraper_service._update_pair_risk(
            session, pair, RiskLevel.RISK_ZONE, SignalType.ST_WARNING, "Test Alert"
        )
        
        assert changed is True
        assert pair.risk_level == RiskLevel.RISK_ZONE
        # Должен быть добавлен в сессию
        session.add.assert_called_with(pair)
        
        # 2. Попытка понизить риск (RISK_ZONE -> CROSS_RISK) - не должно сработать
        # Так как RISK_ZONE (3) > CROSS_RISK (1)
        changed_lower = await scraper_service._update_pair_risk(
            session, pair, RiskLevel.CROSS_RISK, SignalType.ST_WARNING, "Lower Alert"
        )
        
        assert changed_lower is False
        assert pair.risk_level == RiskLevel.RISK_ZONE # Остался прежним
