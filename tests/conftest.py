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


# --- Engine: in-memory SQLite для изоляции тестов ---
# check_same_thread=False нужен, т.к. aiosqlite работает в отдельном потоке
TEST_DB_URL = "sqlite+aiosqlite://"

test_engine = create_async_engine(
    TEST_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


@pytest_asyncio.fixture
async def db_session():
    """
    Фикстура сессии БД.
    
    Перед каждым тестом:
      1. Создаёт все таблицы в памяти
      2. Отдаёт сессию для работы
    После теста:
      3. Удаляет все таблицы (полная очистка)
    """
    # Создаём таблицы
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Создаём сессию
    async_session = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session

    # Очистка — дропаем все таблицы
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest.fixture
def session_factory(db_session: AsyncSession):
    """
    Фикстура, имитирующая database.core.get_session.
    
    Сервисы принимают session_factory в конструктор.
    Эта фикстура возвращает контекстный менеджер,
    который всегда отдаёт ту же тестовую сессию.
    """
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory
