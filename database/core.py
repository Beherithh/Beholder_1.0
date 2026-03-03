from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event
from sqlalchemy.pool import NullPool

# Импортируем все модели, чтобы SQLAlchemy знал о таблицах
from database.models import MonitoredPair, AppSettings, Signal, DelistingEvent

# Имя файла базы данных SQLite
sqlite_file_name = "database.db"
# Строка подключения. 
# sqlite+aiosqlite означает, что мы используем драйвер aiosqlite для асинхронной работы с SQLite
sqlite_url = f"sqlite+aiosqlite:///{sqlite_file_name}"



# Использование NullPool КРИТИЧНО для SQLite в WAL режиме с aiosqlite, 
# чтобы соединения закрывались сразу после `async with`, сбрасывая блокировки,
# и позволяя базе делать WAL Checkpoint (иначе .db-wal разрастается до гигабайт).
engine = create_async_engine(
    sqlite_url, 
    echo=False, 
    connect_args={"timeout": 30},
    poolclass=NullPool 
)

# SessionFactory создаётся ОДИН РАЗ на уровне модуля — не при каждом вызове get_session!
async_session_factory = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

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
    # Явно включаем авто-чекпоинт каждые 1000 страниц (около 4 МБ)
    cursor.execute("PRAGMA wal_autocheckpoint=1000")
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
    
    # Список ключей, которые должны быть в БД для корректной работы UI.
    # Сами значения по умолчанию теперь управляются через ConfigService (services/config.py).
    keys = [
        "cmc_rank_threshold",
        "update_interval_hours",
        "scraper_interval_hours",
        "cmc_update_interval_days",
        "watched_files"
    ]
    
    async with get_session() as session:
        for key in keys:
            stmt = select(AppSettings).where(AppSettings.key == key)
            existing = (await session.execute(stmt)).scalars().first()
            if not existing:
                # По умолчанию ставим None (строкой), чтобы ConfigService использовал свой дефолт.
                # Исключение — watched_files, который должен быть валидным JSON-списком.
                value = "[]" if key == "watched_files" else "None"
                session.add(AppSettings(key=key, value=value))
        
        await session.commit()

from contextlib import asynccontextmanager

@asynccontextmanager
async def get_session() -> AsyncSession:
    """
    Генератор сессий базы данных.
    Используется c конструкцией 'async with' для выполнения запросов.
    """
    async with async_session_factory() as session:
        yield session
