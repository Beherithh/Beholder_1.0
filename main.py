from nicegui import ui, app
from database.core import init_db
from ui.pages.dashboard import dashboard_page # Register Dashboard as Home
from ui.pages.settings import settings_page # Register page
from ui.pages.logs import logs_page # Register Logs page
from ui.layout import create_header
from services.system import init_services, get_scheduler




async def startup():
    print("Инициализация Базы Данных...")
    await init_db()
    
    print("Запуск сервисов...")
    await init_services()
    
    # Инициализация перехвата логов для UI
    from ui.pages.logs import init_logging
    init_logging()

    # Добавляем оповещения (Toasts) для Warning/Error
    from loguru import logger
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
    ui.run(title='Beholder Dashboard', port=8080, reload=True, show=False)  # show=False чтобы не открывал вкладку автоматически при рестарте
