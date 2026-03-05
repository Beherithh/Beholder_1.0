from nicegui import ui
from ui.layout import create_header


# Глобальный список для хранения последних логов
LOG_BUFFER = []
MAX_LOG_LINES = 1000
active_log_elements = []

class ReverseLog:
    """
    Обертка для отображения логов в обратном порядке (новые сверху).
    """
    def __init__(self, container, max_lines=1000):
        self.container = container
        self.max_lines = max_lines
        self.labels = [] 

    def push(self, text: str):
        with self.container:
            # Создаем метку с увеличенным шрифтом (text-sm) и отступами
            lbl = ui.label(text).classes('font-mono text-sm text-white whitespace-pre-wrap border-b border-gray-700 pb-2 mb-1')
            # Перемещаем её в начало контейнера
            lbl.move(target_index=0)
            # Сохраняем ссылку
            self.labels.insert(0, lbl)
            
        # Удаляем старые, если превышен лимит
        if len(self.labels) > self.max_lines:
            oldest = self.labels.pop()
            oldest.delete()
            
    @property
    def client(self):
        return self.container.client

class FilteredLogViewer:
    """
    Универсальный компонент для отображения отфильтрованных логов.
    Используется для страниц Ошибок и Предупреждений.
    """
    def __init__(self, container, max_lines=500, levels=None):
        self.container = container
        self.max_lines = max_lines
        self.levels = levels or [] # Список уровней, например ['ERROR', 'WARNING']
        self.labels = []
        self.counts = {l: 0 for l in self.levels}
        self.counters_elements = {}
        
    def push(self, text: str):
        # Фильтруем по уровню лога
        if self._should_show_log(text):
            with self.container:
                # Определяем цвет в зависимости от уровня
                color_class = self._get_log_color(text)
                lbl = ui.label(text).classes(
                    f'font-mono text-sm {color_class} whitespace-pre-wrap border-b border-gray-700 pb-2 mb-1'
                )
                # Перемещаем её в начало контейнера
                lbl.move(target_index=0)
                # Сохраняем ссылку
                self.labels.insert(0, lbl)
                
                # Обновляем счетчики
                for level in self.counts:
                    if f" {level}:" in text or f"[{level}]" in text:
                         self.counts[level] += 1
                         self._update_counter(level)
                
            # Удаляем старые, если превышен лимит
            if len(self.labels) > self.max_lines:
                oldest = self.labels.pop()
                oldest.delete()
    
    def set_counter(self, level, element):
        """Связывает счетчик UI с уровнем лога"""
        self.counters_elements[level] = element
        self._update_counter(level)
    
    def _update_counter(self, level):
        if level in self.counters_elements:
            self.counters_elements[level].text = f'{level}: {self.counts[level]}'
    
    def _should_show_log(self, text: str) -> bool:
        """Проверяем, нужно ли показывать этот лог"""
        if not self.levels: return True
        for level in self.levels:
            if f" {level}:" in text or f"[{level}]" in text:
                return True
        return False
    
    def _get_log_color(self, text: str) -> str:
        """Определяем цвет в зависимости от уровня лога"""
        if "ERROR" in text or "CRITICAL" in text:
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
        for level in self.counts:
            self.counts[level] = 0
            self._update_counter(level)

def _format_log_message(message) -> str:
    """Извлекает текст из объекта сообщения Loguru.

    Loguru может передавать как строку, так и объект с атрибутом `record`.
    Эта функция унифицирует формат для всех UI-обработчиков.
    """
    try:
        if hasattr(message, 'record'):
            r = message.record
            return f"[{r['time'].strftime('%H:%M:%S')}] {r['level'].name}: {r['message']}"
    except Exception:
        pass
    return str(message)


def _broadcast_to_viewers(viewers: list, text: str) -> list:
    """Рассылает текст всем активным UI-элементам и возвращает список живых.

    Автоматически убирает элементы, чьи клиенты отключились.
    """
    keep = []
    for el in viewers:
        try:
            if el.client.has_socket_connection:
                el.push(text)
                keep.append(el)
        except Exception:
            pass
    return keep


def broadcast_log(message):
    """
    Функция-обработчик для Loguru. Рассылает логи всем активным клиентам.
    """
    global active_log_elements
    
    text = _format_log_message(message)

    # Определяем уровень — INFO/DEBUG идут на страницу логов
    is_info_or_debug = True
    try:
        if hasattr(message, 'record'):
            if message.record['level'].name in ("WARNING", "ERROR", "CRITICAL"):
                is_info_or_debug = False
    except Exception:
        pass
        
    # Сохраняем в буфер (все уровни — для истории)
    LOG_BUFFER.append(text)
    if len(LOG_BUFFER) > MAX_LOG_LINES:
        LOG_BUFFER.pop(0)
        
    # Рассылаем по активным UI элементам (только INFO/DEBUG)
    if is_info_or_debug:
        active_log_elements = _broadcast_to_viewers(active_log_elements, text)

@ui.page('/logs')
def logs_page():
    create_header()
    
    with ui.column().classes('w-full h-screen p-4'):
        ui.label('Системные логи').classes('text-xl font-bold mb-2')
        ui.label('Отображение: INFO и DEBUG (без ошибок и предупреждений)').classes('text-sm text-gray-400')
        
        # Контейнер для логов с прокруткой
        with ui.scroll_area().classes('w-full h-full bg-gray-900 rounded shadow-inner border border-gray-700'):
            log_container = ui.column().classes('w-full p-2 gap-1')
            
        reverse_logger = ReverseLog(log_container, max_lines=1000)
        
        # Заполняем историей (фильтруем)
        for line in LOG_BUFFER:
            if " WARNING:" not in line and " ERROR:" not in line and " CRITICAL:" not in line:
                reverse_logger.push(line)
            
        # Регистрируем для получения новых логов
        active_log_elements.append(reverse_logger)
