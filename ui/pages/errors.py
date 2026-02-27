from nicegui import ui
from ui.layout import create_header
from ui.pages.logs import LOG_BUFFER, FilteredLogViewer

# Глобальные переменные для страницы ошибок
error_log_elements = []

def broadcast_error_log(message):
    """
    Функция-обработчик для рассылки только ERROR и CRITICAL логов.
    """
    global error_log_elements
    
    text = message
    try:
        if hasattr(message, 'record'):
            r = message.record
            text = f"[{r['time'].strftime('%H:%M:%S')}] {r['level'].name}: {r['message']}"
    except:
        pass
    
    # Рассылаем по активным UI элементам
    keep_list = []
    for el in error_log_elements:
        try:
            if el.client.has_socket_connection:
                el.push(text)
                keep_list.append(el)
        except:
            pass
    error_log_elements = keep_list

@ui.page('/errors')
def errors_page():
    create_header()

    with ui.column().classes('w-full h-screen p-4'):
        with ui.row().classes('w-full justify-between items-center mb-4'):
            ui.label('Ошибки').classes('text-xl font-bold')
            ui.label('Только ERROR и CRITICAL (новые сверху)').classes('text-sm text-gray-400')
            
            ui.button('Очистить', on_click=lambda: log_viewer.clear()).classes(
                'bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded'
            )
        
        # Статистика
        with ui.row().classes('w-full gap-4 mb-4'):
            error_count = ui.label('ERROR: 0').classes('text-red-400 font-bold')
        
        # Контейнер
        with ui.scroll_area().classes('w-full h-full bg-gray-900 rounded shadow-inner border border-gray-700'):
            log_container = ui.column().classes('w-full p-2 gap-1')
            
        log_viewer = FilteredLogViewer(log_container, max_lines=500, levels=['ERROR', 'CRITICAL'])
        log_viewer.set_counter('ERROR', error_count)
        log_viewer.set_counter('CRITICAL', error_count)
        
        # Заполняем историей
        for line in LOG_BUFFER:
            if log_viewer._should_show_log(line):
                log_viewer.push(line)
        
        error_log_elements.append(log_viewer)
