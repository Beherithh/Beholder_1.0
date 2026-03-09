from nicegui import ui
from ui.layout import create_header
from ui.pages.logs import LOG_BUFFER, FilteredLogViewer, _format_log_message, _broadcast_to_viewers

# Глобальный список для активных элементов страницы предупреждений
warning_log_elements = []

def broadcast_warning_log(message):
    """
    Функция-обработчик для рассылки только WARNING логов.
    """
    global warning_log_elements
    text = _format_log_message(message)
    warning_log_elements = _broadcast_to_viewers(warning_log_elements, text)

@ui.page('/warnings')
def warnings_page():
    create_header()

    with ui.column().classes('w-full h-screen p-4'):
        with ui.row().classes('w-full justify-between items-center mb-4'):
            ui.label('Предупреждения').classes('text-xl font-bold')
            ui.label('Только WARNING (новые сверху)').classes('text-sm text-gray-400')
            
            ui.button('Очистить', on_click=lambda: log_viewer.clear()).classes(
                'bg-yellow-600 hover:bg-yellow-700 text-white px-4 py-2 rounded'
            )
        
        with ui.row().classes('w-full gap-4 mb-4'):
            warning_count = ui.label('WARNING: 0').classes('text-yellow-400 font-bold')
        
        with ui.scroll_area().classes('w-full h-full bg-gray-900 rounded shadow-inner border border-gray-700'):
            log_container = ui.column().classes('w-full p-2 gap-1')
            
        log_viewer = FilteredLogViewer(log_container, max_lines=150, levels=['WARNING'])
        log_viewer.set_counter('WARNING', warning_count)
        
        # Заполняем историей
        for line in LOG_BUFFER:
            if log_viewer._should_show_log(line):
                log_viewer.push(line)
        
        warning_log_elements.append(log_viewer)
