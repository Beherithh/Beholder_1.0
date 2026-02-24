from nicegui import ui
from loguru import logger
from ui.layout import create_header
from ui.pages.logs import LOG_BUFFER, ReverseLog, init_logging
from datetime import datetime
import re

# Глобальные переменные для страницы ошибок
error_log_elements = []

class FilteredLogViewer:
    """
    Компонент для отображения отфильтрованных логов (ERROR и WARNING).
    """
    def __init__(self, container, max_lines=500, levels=['ERROR', 'WARNING']):
        self.container = container
        self.max_lines = max_lines
        self.levels = levels
        self.labels = []
        self.error_count = 0
        self.warning_count = 0
        self.counters_elements = None  # Ссылки на элементы счетчиков
        
    def push(self, text: str):
        # Фильтруем по уровню лога
        if self._should_show_log(text):
            with self.container:
                # Определяем цвет в зависимости от уровня
                color_class = self._get_log_color(text)
                lbl = ui.label(text).classes(
                    f'font-mono text-xs {color_class} whitespace-pre-wrap border-b border-gray-700 pb-1'
                )
                # Перемещаем её в начало контейнера
                lbl.move(target_index=0)
                # Сохраняем ссылку
                self.labels.insert(0, lbl)
                
                # Обновляем счетчики
                if "ERROR" in text:
                    self.error_count += 1
                elif "WARNING" in text:
                    self.warning_count += 1
                
                # Обновляем UI счетчиков если они установлены
                self._update_counter_display()
                
            # Удаляем старые, если превышен лимит
            if len(self.labels) > self.max_lines:
                oldest = self.labels.pop()
                oldest.delete()
    
    def set_counters(self, error_element, warning_element):
        """Устанавливает элементы для отображения счетчиков"""
        self.counters_elements = (error_element, warning_element)
        self._update_counter_display()
    
    def _update_counter_display(self):
        """Обновляет отображение счетчиков"""
        if self.counters_elements:
            error_elem, warning_elem = self.counters_elements
            error_elem.text = f'ERROR: {self.error_count}'
            warning_elem.text = f'WARNING: {self.warning_count}'
    
    def _should_show_log(self, text: str) -> bool:
        """Проверяем, нужно ли показывать этот лог"""
        for level in self.levels:
            if f" {level}:" in text or f"[{level}]" in text:
                return True
        return False
    
    def _get_log_color(self, text: str) -> str:
        """Определяем цвет в зависимости от уровня лога"""
        if "ERROR" in text:
            return 'text-red-400'
        elif "WARNING" in text:
            return 'text-yellow-400'
        else:
            return 'text-white'
    
    @property
    def client(self):
        return self.container.client
    
    def clear(self):
        """Очищает все логи"""
        for label in self.labels:
            label.delete()
        self.labels.clear()
        self.error_count = 0
        self.warning_count = 0
        self._update_counter_display()

def broadcast_error_log(message):
    """
    Функция-обработчик для рассылки только ERROR и WARNING логов.
    """
    global error_log_elements
    
    # Форматируем сообщение
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
            # Проверка живой ли элемент
            if el.client.has_socket_connection:
                el.push(text)
                keep_list.append(el)
        except:
            pass
    error_log_elements = keep_list

@ui.page('/errors')
def errors_page():
    create_header()
    
    # Убеждаемся, что логирование инициализировано
    init_logging()

    with ui.column().classes('w-full h-screen p-4'):
        # Заголовок с информацией
        with ui.row().classes('w-full justify-between items-center mb-4'):
            ui.label('Ошибки и Предупреждения').classes('text-xl font-bold')
            ui.label('Только ERROR и WARNING (новые сверху)').classes('text-sm text-gray-400')
            
            # Кнопка очистки
            ui.button('Очистить', on_click=lambda: clear_logs(log_viewer)).classes(
                'bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded'
            )
        
        # Статистика
        with ui.row().classes('w-full gap-4 mb-4'):
            error_count = ui.label('ERROR: 0').classes('text-red-400 font-bold')
            warning_count = ui.label('WARNING: 0').classes('text-yellow-400 font-bold')
        
        # Контейнер для логов с прокруткой
        with ui.scroll_area().classes('w-full h-full bg-gray-900 rounded shadow-inner border border-gray-700'):
            log_container = ui.column().classes('w-full p-2 gap-1')
            
        # Создаем просмотрщик
        log_viewer = FilteredLogViewer(log_container, max_lines=500, levels=['ERROR', 'WARNING'])
        
        # Устанавливаем счетчики
        log_viewer.set_counters(error_count, warning_count)
        
        # Заполняем историей (фильтруем из буфера)
        for line in LOG_BUFFER:
            if log_viewer._should_show_log(line):
                log_viewer.push(line)
        
        # Регистрируем для получения новых логов
        error_log_elements.append(log_viewer)

def clear_logs(log_viewer: FilteredLogViewer):
    """Очищает логи и обновляет счетчики"""
    log_viewer.clear()
