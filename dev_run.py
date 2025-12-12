import asyncio
import os
from database.core import init_db, get_session
from services.file_watcher import FileWatcherService
from loguru import logger

async def main():
    # Инициализация БД
    await init_db()
    
    # Путь к нашему существующему файлу
    local_file = os.path.abspath("Gate_instruments_USDT")
    
    logger.info("Запуск теста FileWatcherService...")
    logger.info(f"Пробуем читать файл: {local_file}")
    
    watcher = FileWatcherService(get_session)
    
    # Запускаем синхронизацию
    stats = await watcher.sync_files([local_file])
    
    logger.success(f"Результат: {stats}")

if __name__ == "__main__":
    asyncio.run(main())
