from nicegui import ui, app
from database.core import init_db
from ui.pages.settings import settings_page # Register page

@ui.page('/')
def main_page():
    with ui.header().classes('bg-gray-800 text-white p-4 flex items-center gap-4'):
        ui.label('Beholder').classes('text-xl font-bold')
        ui.link('Дашборд', '/').classes('text-white no-underline hover:text-gray-300')
        ui.link('Настройки', '/settings').classes('text-white no-underline hover:text-gray-300')

    with ui.column().classes('w-full items-center mt-10'):
        ui.label('Добро пожаловать в Beholder').classes('text-4xl font-bold text-gray-700')
        ui.label('Перейдите в настройки для добавления файлов').classes('text-gray-500 mt-2')
        ui.button('Перейти в Настройки', on_click=lambda: ui.open('/settings')).classes('mt-4 bg-blue-600')

from services.system import init_services, get_scheduler

async def startup():
    print("Инициализация Базы Данных...")
    await init_db()
    
    print("Запуск сервисов...")
    init_services()
    
    # Запуск планировщика
    scheduler = get_scheduler()
    scheduler.start()
    await scheduler.schedule_all() # Шедулим ВСЕ задачи (рынок + скрапер)
    
    print("Система Beholder запущена.")

app.on_startup(startup)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title='Beholder Dashboard', port=8080, reload=True, show=False)  # show=False чтобы не открывал вкладку автоматически при рестарте
