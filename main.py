from nicegui import ui, app
from database.core import init_db

@ui.page('/')
def main_page():
    ui.label('Beholder Project').classes('text-4xl font-bold')
    ui.label('System Initialization...').classes('text-gray-500')

async def startup():
    print("Initializing Database...")
    await init_db()
    print("Database Initialized.")

app.on_startup(startup)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title='Beholder', port=8080)
