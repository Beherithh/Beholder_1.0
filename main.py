from nicegui import ui, app
from loguru import logger

# Централизованная настройка логирования
from utils.logging_setup import init_logging
init_logging()

from database.core import init_db, engine
from services.system import init_services, services
from ui.pages.dashboard import dashboard_page
from ui.pages.signals import signals_page
from ui.pages.pivot import pivot_page
from ui.pages.settings import settings_page
from ui.pages.manual_controls import manual_controls_page
from ui.pages.logs import logs_page
from ui.pages.errors import errors_page
from ui.pages.warnings import warnings_page

async def startup():
    logger.info("Initializing Database...")
    await init_db()
    
    logger.info("Starting services...")
    await init_services()

    logger.info("Syncing monitored pairs...")
    await services.file_watcher.sync_from_settings()
    
    # Добавляем оповещения (Toasts) для Warning/Error
    def ui_notification_sink(message):
        record = message.record
        try:
            ui.notify(record["message"], type='warning' if record["level"].name == "WARNING" else 'negative', position='bottom-right')
        except:
            pass
    
    # Этот обработчик добавляется после основной инициализации,
    # так как он зависит от UI-контекста, который может быть не всегда доступен.
    logger.add(ui_notification_sink, level="WARNING")
    
    # Запуск планировщика
    services.scheduler.start()
    await services.scheduler.schedule_all()
    
    logger.info("Система Beholder запущена.")

async def shutdown():
    logger.info("Система Beholder останавливается...")
    services.scheduler.stop()
    await engine.dispose()
    logger.success("Все соединения с базой данных закрыты. WAL-файл сброшен.")

app.on_startup(startup)
app.on_shutdown(shutdown)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title='Beholder Dashboard', port=8080, reload=False, show=False, fastapi_docs=False)
