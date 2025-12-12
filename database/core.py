from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Имя файла базы данных SQLite
sqlite_file_name = "database.db"
# Строка подключения. 
# sqlite+aiosqlite означает, что мы используем драйвер aiosqlite для асинхронной работы с SQLite
sqlite_url = f"sqlite+aiosqlite:///{sqlite_file_name}"

# Создаем "движок" базы данных.
# echo=False отключает вывод всех SQL запросов в консоль (можно включить True для отладки)
engine = create_async_engine(sqlite_url, echo=False)

async def init_db():
    """
    Функция инициализации базы данных.
    Создает все таблицы, описанные в моделях SQLModel, если они еще не существуют.
    """
    async with engine.begin() as conn:
        # await conn.run_sync(SQLModel.metadata.drop_all) # ВНИМАНИЕ: Раскомментировать только для полного сброса БД (удалит все данные!)
        await conn.run_sync(SQLModel.metadata.create_all)

from contextlib import asynccontextmanager

@asynccontextmanager
async def get_session() -> AsyncSession:
    """
    Генератор сессий базы данных.
    Используется c конструкцией 'async with' для выполнения запросов.
    """
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
