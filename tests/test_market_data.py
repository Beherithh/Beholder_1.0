"""
Тесты сервиса рыночных данных (MarketDataService).

Проверяют:
1. Вычисление времени последней свечи (_get_last_candle_time):
   - Расчёт дефолтного окна (30 дней назад) для новых пар.
   - Корректное определение времени последней свечи при наличии истории.
   - Нормализацию часовых поясов (offset-naive -> UTC offset-aware).
2. Расчёт множителя объёма (_get_volume_multiplier):
   - Для спот-рынков (SPOT) множитель всегда 1.0.
   - Для фьючерсов (LINEAR) множитель извлекается из contractSize биржи.
3. Обновление истории свечей (update_pair_history):
   - Фильтрацию уже имеющихся свечей (дедупликация).
   - Игнорирование текущей незавершённой свечи.
   - Корректную запись новых свечей в SQLite.
   - Безопасную обработку сетевых сбоев CCXT (NetworkError).
4. Ротацию и очистку устаревших данных (cleanup_old_market_data).
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MonitoredPair, MarketData, MarketType
from services.market_data import MarketDataService
from services.config import ConfigService


@pytest.fixture
def market_data_service(session_factory, config_service: ConfigService):
    """Фикстура для создания MarketDataService с тестовой БД."""
    return MarketDataService(session_factory=session_factory, config_service=config_service)


class TestGetLastCandleTime:
    """Тестирование логики поиска последней свечи."""

    @pytest.mark.asyncio
    async def test_returns_30_days_ago_when_no_candles(
        self,
        db_session: AsyncSession,
        market_data_service: MarketDataService,
        create_pair,
    ) -> None:
        """Если в БД нет свечей по паре, возвращается отметка примерно 30 дней назад в UTC."""
        pair: MonitoredPair = await create_pair(symbol="NEW/USDT", exchange="BINANCE")

        last_time = await market_data_service._get_last_candle_time(db_session, pair.id)

        assert last_time.tzinfo is not None, "Timestamp обязан быть offset-aware (UTC)"
        expected_approx = datetime.now(timezone.utc) - timedelta(days=30)
        # Разница должна быть не больше нескольких секунд
        assert abs((last_time - expected_approx).total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_returns_latest_candle_time_when_candles_exist(
        self,
        db_session: AsyncSession,
        market_data_service: MarketDataService,
        create_pair,
    ) -> None:
        """При наличии свечей возвращается время самой свежей свечи."""
        pair: MonitoredPair = await create_pair(symbol="ETH/USDT", exchange="BINANCE")
        now = datetime.now(timezone.utc)

        candle_old = MarketData(
            pair_id=pair.id,
            timestamp=now - timedelta(days=5),
            open=3000.0, high=3100.0, low=2900.0, close=3050.0, volume=100.0,
        )
        candle_latest = MarketData(
            pair_id=pair.id,
            timestamp=now - timedelta(hours=2),
            open=3050.0, high=3200.0, low=3000.0, close=3150.0, volume=200.0,
        )
        db_session.add_all([candle_old, candle_latest])
        await db_session.commit()

        last_time = await market_data_service._get_last_candle_time(db_session, pair.id)
        assert last_time == candle_latest.timestamp


class TestVolumeMultiplier:
    """Тестирование логики вычисления множителя объёма (contractSize)."""

    def test_spot_market_always_returns_one(self, market_data_service: MarketDataService) -> None:
        """Для спот-рынков множитель объёма всегда строго 1.0."""
        mock_exchange = MagicMock()
        mock_exchange.markets = {"BTC/USDT": {"contractSize": 10.0}}

        multiplier = market_data_service._get_volume_multiplier(
            exchange=mock_exchange,
            normalized_symbol="BTC/USDT",
            market_type=MarketType.SPOT,
        )
        assert multiplier == 1.0

    def test_futures_market_reads_contract_size(self, market_data_service: MarketDataService) -> None:
        """Для деривативов множитель извлекается из параметров контракта биржи."""
        mock_exchange = MagicMock()
        mock_exchange.markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}

        multiplier = market_data_service._get_volume_multiplier(
            exchange=mock_exchange,
            normalized_symbol="BTC/USDT:USDT",
            market_type=MarketType.LINEAR,
        )
        assert multiplier == 0.01

    def test_futures_market_fallback_on_missing_symbol(self, market_data_service: MarketDataService) -> None:
        """Если инструмент не найден в кэше рынков, возвращается безопасный дефолт 1.0."""
        mock_exchange = MagicMock()
        mock_exchange.markets = {}

        multiplier = market_data_service._get_volume_multiplier(
            exchange=mock_exchange,
            normalized_symbol="UNKNOWN/USDT:USDT",
            market_type=MarketType.LINEAR,
        )
        assert multiplier == 1.0


class TestUpdatePairHistory:
    """Тестирование загрузки и сохранения свечей через CCXT."""

    @pytest.mark.asyncio
    async def test_saves_new_candles_and_filters_duplicates(
        self,
        db_session: AsyncSession,
        market_data_service: MarketDataService,
        create_pair,
    ) -> None:
        """
        Проверяет:
        1. Пропуск свечей, которые уже были записаны (<= last_time).
        2. Игнорирование текущей незавершённой свечи.
        3. Запись валидной завершённой свечи в SQLite.
        """
        pair: MonitoredPair = await create_pair(symbol="SOL/USDT", exchange="BINANCE")

        now = datetime.now(timezone.utc)
        start_of_current_hour = now.replace(minute=0, second=0, microsecond=0)

        # Добавляем в БД свечу двухчасовой давности
        existing_time = start_of_current_hour - timedelta(hours=2)
        db_session.add(MarketData(
            pair_id=pair.id,
            timestamp=existing_time,
            open=100.0, high=105.0, low=95.0, close=102.0, volume=500.0,
        ))
        await db_session.commit()

        # Мокаем биржу CCXT
        mock_exchange = MagicMock()
        mock_exchange.rateLimit = 0
        mock_exchange.markets = {}

        # 3 свечи от биржи:
        # 1-я: старая (дубликат)
        ts_old_ms = int(existing_time.timestamp() * 1000)
        # 2-я: новая завершённая свеча (за прошлый час)
        candle_new_time = start_of_current_hour - timedelta(hours=1)
        ts_new_ms = int(candle_new_time.timestamp() * 1000)
        # 3-я: текущая незавершённая свеча (должна быть пропущена)
        ts_current_ms = int(start_of_current_hour.timestamp() * 1000)

        mock_candles = [
            [ts_old_ms, 100.0, 105.0, 95.0, 102.0, 500.0],
            [ts_new_ms, 102.0, 110.0, 101.0, 108.0, 800.0],
            [ts_current_ms, 108.0, 109.0, 107.0, 108.5, 100.0],
        ]

        # В первой пачке отдаем свечи, во второй пустой список для завершения цикла
        mock_exchange.fetch_ohlcv = AsyncMock(side_effect=[mock_candles, []])

        # Запускаем обновление истории
        saved_count = await market_data_service.update_pair_history(db_session, mock_exchange, pair)

        assert saved_count == 1, "Должна быть сохранена ровно 1 новая свеча"

        # Проверяем записи в БД
        stmt = select(MarketData).where(MarketData.pair_id == pair.id).order_by(MarketData.timestamp.asc())
        all_candles = (await db_session.execute(stmt)).scalars().all()
        assert len(all_candles) == 2
        assert all_candles[1].close == 108.0

    @pytest.mark.asyncio
    async def test_handles_ccxt_exception_gracefully(
        self,
        db_session: AsyncSession,
        market_data_service: MarketDataService,
        create_pair,
    ) -> None:
        """Сетевая ошибка CCXT ловится, логируется и метод возвращает 0 без падения."""
        pair: MonitoredPair = await create_pair(symbol="XRP/USDT", exchange="BINANCE")

        mock_exchange = MagicMock()
        mock_exchange.markets = {}
        mock_exchange.fetch_ohlcv = AsyncMock(side_effect=RuntimeError("Биржа временно недоступна (502 Bad Gateway)"))

        saved_count = await market_data_service.update_pair_history(db_session, mock_exchange, pair)
        assert saved_count == 0


class TestCleanupOldMarketData:
    """Тестирование очистки архивных данных."""

    @pytest.mark.asyncio
    async def test_cleanup_deletes_only_expired_candles(
        self,
        db_session: AsyncSession,
        market_data_service: MarketDataService,
        create_pair,
    ) -> None:
        """Свечи старше 180 дней удаляются, свежие — остаются."""
        pair: MonitoredPair = await create_pair(symbol="LTC/USDT", exchange="BINANCE")
        now = datetime.now(timezone.utc)

        ancient_candle = MarketData(
            pair_id=pair.id,
            timestamp=now - timedelta(days=200),  # Старше 180 дней
            open=100.0, high=100.0, low=100.0, close=100.0, volume=10.0,
        )
        fresh_candle = MarketData(
            pair_id=pair.id,
            timestamp=now - timedelta(days=30),   # Свежая
            open=100.0, high=100.0, low=100.0, close=100.0, volume=10.0,
        )
        db_session.add_all([ancient_candle, fresh_candle])
        await db_session.commit()

        # Запускаем очистку старше 180 дней
        await market_data_service.cleanup_old_market_data(days=180)

        stmt = select(MarketData).where(MarketData.pair_id == pair.id)
        remaining = (await db_session.execute(stmt)).scalars().all()

        assert len(remaining) == 1
        assert remaining[0].timestamp == fresh_candle.timestamp
