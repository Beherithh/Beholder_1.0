"""
Тестовая инфраструктура Beholder.

Ключевой принцип: используем in-memory SQLite, чтобы тесты были быстрыми
и изолированными. Каждый тест получает чистую БД.
"""
import pytest
import pytest_asyncio
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from contextlib import asynccontextmanager
from unittest.mock import patch

from services.config import ConfigService
from database.models import AppSettings

# --- Engine: in-memory SQLite для изоляции тестов ---
TEST_DB_URL = "sqlite+aiosqlite://"

test_engine = create_async_engine(
    TEST_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

@pytest_asyncio.fixture(scope="function")
async def db_session():
    """
    Фикстура сессии БД.
    Перед каждым тестом: создает таблицы, отдает сессию.
    После теста: удаляет все таблицы.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async_session = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

@pytest.fixture(scope="function")
def session_factory(db_session: AsyncSession):
    """
    Фикстура, имитирующая database.core.get_session.
    Возвращает контекстный менеджер, который всегда отдает ту же тестовую сессию.
    """
    @asynccontextmanager
    async def _factory():
        yield db_session
    return _factory

@pytest.fixture(scope="function")
def config_service(session_factory):
    """Фикстура, создающая экземпляр ConfigService для тестов."""
    return ConfigService(session_factory)

@pytest.fixture(autouse=True)
def mock_get_config_service(config_service):
    """
    Автоматически мокает get_config_service во всех тестах,
    чтобы он возвращал тестовый экземпляр.
    """
    with patch("services.system.get_config_service") as mock:
        mock.return_value = config_service
        yield mock

@pytest.fixture
def create_pair(db_session: AsyncSession):
    """Фабрика для создания мониторимой пары в БД."""
    async def _create(symbol: str = "BTC/USDT", exchange: str = "BINANCE", risk_level=None):
        from database.models import MonitoredPair, RiskLevel
        pair = MonitoredPair(
            exchange=exchange,
            symbol=symbol,
            source_file="test.json",
            risk_level=risk_level or RiskLevel.NORMAL
        )
        db_session.add(pair)
        await db_session.commit()
        await db_session.refresh(pair)
        return pair
    return _create

@pytest.fixture
def setup_defaults(db_session: AsyncSession):
    """Фикстура для инициализации дефолтных настроек."""
    async def _setup():
        defaults = {
            "cmc_rank_threshold": "500",
            "alert_price_hours_pump_period": "6",
            "alert_price_hours_pump_threshold": "50",
            "alert_price_hours_dump_period": "6",
            "alert_price_hours_dump_threshold": "50",
        }
        for key, value in defaults.items():
            db_session.add(AppSettings(key=key, value=value))
        await db_session.commit()
    return _setup
