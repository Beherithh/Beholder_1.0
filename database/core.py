from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event

# Имя файла базы данных SQLite
sqlite_file_name = "database.db"
# Строка подключения. 
# sqlite+aiosqlite означает, что мы используем драйвер aiosqlite для асинхронной работы с SQLite
sqlite_url = f"sqlite+aiosqlite:///{sqlite_file_name}"



# Создаем "движок" базы данных.
# connect_args={"timeout": 30} позволяет SQLite ждать до 30 секунд, если база заблокирована другим процессом
engine = create_async_engine(sqlite_url, echo=False, connect_args={"timeout": 30})

@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    # foreign_keys=ON: поддержка связей между таблицами
    cursor.execute("PRAGMA foreign_keys=ON")
    # journal_mode=WAL: режим Write-Ahead Logging. Позволяет нескольким читателям и одному писателю работать одновременно.
    # Это КРИТИЧНО для предотвращения ошибок "database is locked".
    cursor.execute("PRAGMA journal_mode=WAL")
    # synchronous=NORMAL: ускоряет запись в режиме WAL, сохраняя при этом безопасность.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

async def init_db():
    """
    Функция инициализации базы данных.
    Создает все таблицы, описанные в моделях SQLModel, если они еще не существуют.
    """
    async with engine.begin() as conn:
        # await conn.run_sync(SQLModel.metadata.drop_all) # ВНИМАНИЕ: Раскомментировать только для полного сброса БД (удалит все данные!)
        await conn.run_sync(SQLModel.metadata.create_all)
    
    # Инициализация настроек по умолчанию
    await _ensure_default_settings()

async def _ensure_default_settings():
    """Проверяет наличие базовых настроек в БД и создает их, если нет."""
    from database.models import AppSettings
    from sqlalchemy import select
    
    defaults = {
        "cmc_rank_threshold": "500",
        "alert_dedup_hours": "12",
        "update_interval_hours": "1",
        "scraper_interval_hours": "1",
        "cmc_update_interval_days": "5",
        "watched_files": "[]"
    }
    
    async with get_session() as session:
        for key, value in defaults.items():
            stmt = select(AppSettings).where(AppSettings.key == key)
            existing = (await session.execute(stmt)).scalars().first()
            if not existing:
                session.add(AppSettings(key=key, value=value))
            elif existing.value in (None, "None", ""):
                existing.value = value
        
        await session.commit()

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
