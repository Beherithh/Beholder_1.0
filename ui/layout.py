from nicegui import ui

# CSS классы для всех ссылок в меню
LINK_CLASSES = 'text-white no-underline hover:bg-gray-700 p-2 rounded w-full block'

# Структура меню для легкого добавления/удаления пунктов
MENU_ITEMS = [
    {"icon": "🏠", "text": "Дашборд", "path": "/"},
    {"icon": "📊", "text": "Сигналы", "path": "/signals"},
    {"icon": "⚙️", "text": "Настройки", "path": "/settings"},
    {"icon": "▶️", "text": "Кнопки", "path": "/manual"},
    {"icon": "📋", "text": "Логи", "path": "/logs"},
    {"icon": "⚠️", "text": "Предупреждения", "path": "/warnings"},
    {"icon": "🚨", "text": "Ошибки", "path": "/errors"},
]

def create_header():
    """Создает левую боковую панель навигации"""
    with ui.left_drawer(value=True).classes('bg-gray-800 text-white').props('width=180'):
        ui.label('Beholder').classes('text-2xl font-bold p-4 border-b border-gray-700')
        
        with ui.column().classes('p-2 gap-1 w-full'):
            # Генерируем ссылки в цикле
            for item in MENU_ITEMS:
                ui.link(f'{item["icon"]} {item["text"]}', item["path"]).classes(LINK_CLASSES)
