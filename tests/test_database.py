"""
Тесты Database Layer (database.core и ограничения SQLite).

Проверяют:
1. Реальную функцию _ensure_default_settings из database.core (без копипасты!).
2. Идемпотентность инициализации настроек: пользовательские значения не затираются.
3. Составное уникальное ограничение UniqueConstraint("exchange", "symbol", "market_type"):
   попытка добавить пару с совпадающей биржей, тикером и типом рынка вызывает IntegrityError.
4. Каскадное удаление (CASCADE DELETE): удаление MonitoredPair автоматически
   удаляет связанные с ней записи свечей MarketData.
"""

import pytest
from datetime import datetime, timezone
from sqlmodel import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.core import _ensure_default_settings
from database.models import AppSettings, MonitoredPair, MarketData, MarketType, RiskLevel


@pytest.mark.asyncio
class TestEnsureDefaultSettingsReal:
    """Тестирование реальной функции _ensure_default_settings из database.core."""

    async def test_creates_all_defaults_in_empty_db(
        self,
        db_session: AsyncSession,
        session_factory,
    ) -> None:
        """В пустой БД создаются все 5 обязательных ключей конфигурации."""
        await _ensure_default_settings(session_factory=session_factory)

        expected_keys = [
            "cmc_rank_threshold",
            "update_interval_hours",
            "scraper_interval_hours",
            "cmc_update_interval_days",
            "watched_files",
        ]

        for key in expected_keys:
            stmt = select(AppSettings).where(AppSettings.key == key)
            record = (await db_session.execute(stmt)).scalars().first()
            assert record is not None, f"Ключ '{key}' обязан быть создан в БД"
            if key == "watched_files":
                assert record.value == "[]"
            else:
                assert record.value == "None"

    async def test_idempotent_preserves_custom_values(
        self,
        db_session: AsyncSession,
        session_factory,
    ) -> None:
        """Повторный вызов _ensure_default_settings не должен затирать настройки пользователя."""
        # 1. Первый запуск
        await _ensure_default_settings(session_factory=session_factory)

        # 2. Пользователь меняет настройку в UI
        stmt = select(AppSettings).where(AppSettings.key == "cmc_rank_threshold")
        setting = (await db_session.execute(stmt)).scalar_one()
        setting.value = "100"
        await db_session.commit()

        # 3. Второй запуск при рестарте приложения
        await _ensure_default_settings(session_factory=session_factory)

        # 4. Проверяем, что значение 100 сохранилось, а не сбросилось в None
        await db_session.refresh(setting)
        assert setting.value == "100"


@pytest.mark.asyncio
class TestDatabaseConstraints:
    """Тестирование целостности данных и ограничений SQLite."""

    async def test_monitored_pair_composite_unique_constraint(
        self,
        db_session: AsyncSession,
    ) -> None:
        """
        Составной уникальный ключ: (exchange, symbol, market_type).
        Нельзя создать два одинаковых спотовых тикера на одной бирже.
        """
        pair1 = MonitoredPair(
            exchange="BINANCE",
            symbol="BTC/USDT",
            market_type=MarketType.SPOT,
            source_file="test.json",
        )
        db_session.add(pair1)
        await db_session.commit()

        # Попытка добавить полный дубликат
        pair_duplicate = MonitoredPair(
            exchange="BINANCE",
            symbol="BTC/USDT",
            market_type=MarketType.SPOT,
            source_file="test2.json",
        )
        db_session.add(pair_duplicate)

        with pytest.raises(IntegrityError):
            await db_session.commit()

        await db_session.rollback()

    async def test_different_market_types_allowed_for_same_symbol(
        self,
        db_session: AsyncSession,
    ) -> None:
        """
        Один и тот же символ на одной бирже РАЗРЕШЕН, если типы рынка отличаются
        (например, BTC/USDT SPOT и BTC/USDT LINEAR фьючерс).
        """
        spot_pair = MonitoredPair(
            exchange="BYBIT",
            symbol="BTC/USDT",
            market_type=MarketType.SPOT,
            source_file="spot.json",
        )
        futures_pair = MonitoredPair(
            exchange="BYBIT",
            symbol="BTC/USDT",
            market_type=MarketType.LINEAR,
            source_file="futures.json",
        )
        db_session.add_all([spot_pair, futures_pair])
        await db_session.commit()

        assert spot_pair.id is not None
        assert futures_pair.id is not None
        assert spot_pair.id != futures_pair.id

    async def test_cascade_delete_market_data_on_pair_removal(
        self,
        db_session: AsyncSession,
        create_pair,
    ) -> None:
        """
        Каскадное удаление (ON DELETE CASCADE):
        При удалении пары из MonitoredPair все её свечи MarketData должны быть удалены.
        """
        pair: MonitoredPair = await create_pair(symbol="DEL/USDT", exchange="BINANCE")

        candle1 = MarketData(
            pair_id=pair.id,
            timestamp=datetime.now(timezone.utc),
            open=10.0, high=11.0, low=9.0, close=10.5, volume=100.0,
        )
        db_session.add(candle1)
        await db_session.commit()

        # Проверяем наличие свечи
        stmt = select(MarketData).where(MarketData.pair_id == pair.id)
        assert len((await db_session.execute(stmt)).scalars().all()) == 1

        # Удаляем пару
        await db_session.delete(pair)
        await db_session.commit()

        # Свечи также должны исчезнуть
        remaining_candles = (await db_session.execute(stmt)).scalars().all()
        assert len(remaining_candles) == 0, "Свечи удалённой пары обязаны каскадно удалиться"
