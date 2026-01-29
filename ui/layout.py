from nicegui import ui

def create_header():
    """Создает левую боковую панель навигации"""
    with ui.left_drawer(value=True).classes('bg-gray-800 text-white').props('width=180'):
        ui.label('Beholder').classes('text-2xl font-bold p-4 border-b border-gray-700')
        
        with ui.column().classes('p-2 gap-1 w-full'):
            ui.link('🏠 Дашборд', '/').classes('text-white no-underline hover:bg-gray-700 p-2 rounded w-full block')
            ui.link('📊 Сигналы', '/signals').classes('text-white no-underline hover:bg-gray-700 p-2 rounded w-full block')
            ui.link('⚙️ Настройки', '/settings').classes('text-white no-underline hover:bg-gray-700 p-2 rounded w-full block')
            ui.link('▶️ Кнопки', '/manual').classes('text-white no-underline hover:bg-gray-700 p-2 rounded w-full block')
            ui.link('📋 Логи', '/logs').classes('text-white no-underline hover:bg-gray-700 p-2 rounded w-full block')
