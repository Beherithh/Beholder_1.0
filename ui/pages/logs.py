from nicegui import ui
from loguru import logger
import datetime

# Глобальный список для хранения последних логов (чтобы видеть их при перезагрузке страницы)
LOG_BUFFER = []
MAX_LOG_LINES = 1000

class LogSink:
    """
    Кастомный Sink для Loguru, который пишет в буфер и обновляет UI (если он активен).
    """
    def __init__(self, log_element=None):
        self.log_element = log_element

    def write(self, message):
        # 1. Сохраняем в буфер
        # Message от loguru уже отформатирован, но содержит перенос строки
        text = message.record["message"]
        level = message.record["level"].name
        time = message.record["time"].strftime("%H:%M:%S")
        
        line = f"[{time}] {level}: {text}"
        
        LOG_BUFFER.append(line)
        if len(LOG_BUFFER) > MAX_LOG_LINES:
            LOG_BUFFER.pop(0)
            
        # 2. Пишем в UI элемент (если подключен)
        if self.log_element:
            self.log_element.push(line)

# Глобальный синк, который мы зарегистрируем ОДИН раз
# Но проблема: ui.log() создается на страницу.
# Решение: Sink будет заполнять LOG_BUFFER. 
# А страница будет иметь свой Sink? Нет, loguru глобален.
# Сделаем так: Sink добавляет в LOG_BUFFER.
# A ui.timer на странице опрашивает LOG_BUFFER? Или добавляет callback?
# Проще: Sink добавляет в буфер.
# На странице мы рендерим буфер.
# И подписываемся на обновления? 
# Loguru позволяет несколько синков. 
# Самый простой вариант для NiceGUI:
# Использовать ui.log() и метод push.
# Но элемент ui.log существует только в контексте клиента.

# ПОДХОД:
# 1. Функция log_handler(message) - глобальная.
# 2. Страница Logs при создании регистрирует свой ui.log в список "active_loggers".
# 3. Глобальный логгер пишет сразу во все активные ui.log.

active_log_elements = []

def broadcast_log(message):
    global active_log_elements
    
    text = message
    try:
        # Пытаемся взять отформатированный текст или record
        if hasattr(message, 'record'):
            r = message.record
            text = f"[{r['time'].strftime('%H:%M:%S')}] {r['level'].name}: {r['message']}"
    except:
        pass
        
    LOG_BUFFER.append(text)
    if len(LOG_BUFFER) > MAX_LOG_LINES:
        LOG_BUFFER.pop(0)
        
    # Рассылаем по активным UI элементам и чистим мертвые
    keep_list = []
    for el in active_log_elements:
        try:
            # Проверка живой ли элемент (клиент мог отключиться)
            if el.client.has_socket_connection:
                el.push(text)
                keep_list.append(el)
        except:
            # Если ошибка при пуше - считаем элемент мертвым
            pass
    active_log_elements = keep_list

# Регистрируем этот хендлер в Loguru при импорте модуля
# Чтобы не регистрировать дважды, проверим флаг (но модуль импортируется один раз и кешируется)
# Но лучше делать это в init_services или startup.
# Для простоты сделаем тут check.
is_registered = False

def init_logging():
    global is_registered
    if not is_registered:
        logger.add(broadcast_log, format="{message}") # Формат простейший, так как мы сами форматируем
        is_registered = True

@ui.page('/logs')
def logs_page():
    # Header
    from ui.layout import create_header
    create_header()
    
    # Init logging hook if not already
    init_logging()

class ReverseLog:
    """
    Обертка для отображения логов в обратном порядке (новые сверху).
    Использует ui.column c ui.label.
    """
    def __init__(self, container, max_lines=1000):
        self.container = container
        self.max_lines = max_lines
        # Храним ссылки на элементы labels для удаления старых
        self.labels = [] 

    def push(self, text: str):
        # Добавляем новый лог в начало контейнера (move(0))
        with self.container:
            lbl = ui.label(text).classes('font-mono text-xs text-white whitespace-pre-wrap border-b border-gray-700 pb-1')
            lbl.move(target_index=0)
            self.labels.insert(0, lbl)
            
        # Удаляем старые, если превышен лимит
        if len(self.labels) > self.max_lines:
            oldest = self.labels.pop()
            oldest.delete()
            
    # Чтобы mimic ui.log's client property mechanism if needed (active_log_elements check uses el.client)
    @property
    def client(self):
        return self.container.client

@ui.page('/logs')
def logs_page():
    # Header
    from ui.layout import create_header
    create_header()
    
    # Init logging hook if not already
    init_logging()

    with ui.column().classes('w-full h-screen p-4'):
        ui.label('Системные логи').classes('text-xl font-bold mb-2')
        ui.label('Отображение: Новые сверху (Max 1000)').classes('text-sm text-gray-400')
        
        # Контейнер для логов с прокруткой
        with ui.scroll_area().classes('w-full h-full bg-gray-900 rounded shadow-inner border border-gray-700'):
            log_container = ui.column().classes('w-full p-2 gap-1')
            
        # Создаем обертку
        reverse_logger = ReverseLog(log_container, max_lines=1000)
        
        # Заполняем историей (LOG_BUFFER хранит старые->новые, нам надо отобразить reversed)
        # Если мы пушим по очереди:
        # push(Log1) -> [Log1]
        # push(Log2) -> [Log2, Log1]
        # push(Log3) -> [Log3, Log2, Log1]
        # Так что просто итерируемся по буферу.
        for line in LOG_BUFFER:
            reverse_logger.push(line)
            
        # Регистрируем
        active_log_elements.append(reverse_logger)
