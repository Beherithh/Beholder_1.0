from nicegui import ui

def create_header():
    with ui.header().classes('bg-gray-800 text-white p-4 flex items-center gap-4'):
        ui.label('Beholder').classes('text-xl font-bold')
        ui.link('Дашборд', '/').classes('text-white no-underline hover:text-gray-300')
        ui.link('Настройки', '/settings').classes('text-white no-underline hover:text-gray-300')
        ui.link('Логи', '/logs').classes('text-white no-underline hover:text-gray-300')
