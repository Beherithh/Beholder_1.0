from nicegui import ui, app
from loguru import logger

from database.core import init_db
from services.system import init_services, get_scheduler, get_file_watcher_service
from ui.pages.dashboard import dashboard_page # Register Dashboard as Home
from ui.pages.signals import signals_page # Register Signals page
from ui.pages.settings import settings_page # Register page
from ui.pages.manual_controls import manual_controls_page # Register Manual Controls page
from ui.pages.logs import logs_page, init_logging # Register Logs page
from ui.pages.errors import errors_page # Register Errors page
from ui.pages.warnings import warnings_page # Register Warnings page

async def startup():
    print("Initializing Database...")
    await init_db()
    
    print("Starting services...")
    await init_services()

    print("Syncing monitored pairs...")
    watcher = get_file_watcher_service()
    await watcher.sync_from_settings()
    
    # Инициализация перехвата логов для UI
    init_logging()

    # Добавляем оповещения (Toasts) для Warning/Error
    def ui_notification_sink(message):
        record = message.record
        if record["level"].name in ("WARNING", "ERROR", "CRITICAL"):
            try:
                # Пытаемся отправить уведомление в текущий контекст UI
                # Если вызвано из фоновой задачи без контекста, будет pass
                ui.notify(record["message"], type='warning' if record["level"].name == "WARNING" else 'negative', position='bottom-right')
            except:
                pass
    
    logger.add(ui_notification_sink)
    
    # Запуск планировщика
    scheduler = get_scheduler()
    scheduler.start()
    await scheduler.schedule_all() # Шедулим ВСЕ задачи (рынок + скрапер)
    
    print("Система Beholder запущена.")

app.on_startup(startup)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title='Beholder Dashboard', port=8080, reload=False, show=False)
