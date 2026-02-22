from nicegui import ui
from loguru import logger
from ui.layout import create_header
import os

# Глобальный список для хранения последних логов
LOG_BUFFER = []
MAX_LOG_LINES = 1000
active_log_elements = []
is_registered = False

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
            # Создаем метку
            lbl = ui.label(text).classes('font-mono text-xs text-white whitespace-pre-wrap border-b border-gray-700 pb-1')
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

def broadcast_log(message):
    """
    Функция-обработчик для Loguru. Рассылает логи всем активным клиентам.
    """
    global active_log_elements
    
    # Форматируем сообщение
    text = message
    try:
        if hasattr(message, 'record'):
            r = message.record
            text = f"[{r['time'].strftime('%H:%M:%S')}] {r['level'].name}: {r['message']}"
    except:
        pass
        
    # Сохраняем в буфер
    LOG_BUFFER.append(text)
    if len(LOG_BUFFER) > MAX_LOG_LINES:
        LOG_BUFFER.pop(0)
        
    # Рассылаем по активным UI элементам
    keep_list = []
    for el in active_log_elements:
        try:
            # Проверка живой ли элемент (клиент мог отключиться)
            if el.client.has_socket_connection:
                el.push(text)
                keep_list.append(el)
        except:
            pass
    active_log_elements = keep_list

def init_logging():
    """Инициализация перехвата логов (вызывается один раз при старте)"""
    global is_registered
    if not is_registered:
        # 1. Лог в UI (через broadcast_log)
        logger.add(broadcast_log, format="{message}", level="INFO")
        
        # 2. Лог в файл (Ротация: 10 MB или каждый день в 00:00, храним 10 дней)
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        logger.add(
            os.path.join(log_dir, "beholder.log"),
            rotation="10 MB",
            retention="10 days",
            compression="zip",
            level="INFO",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}:{function}:{line} - {message}"
        )
        
        is_registered = True

@ui.page('/logs')
def logs_page():
    create_header()
    
    # Убеждаемся, что логирование инициализировано
    init_logging()

    with ui.column().classes('w-full h-screen p-4'):
        ui.label('Системные логи').classes('text-xl font-bold mb-2')
        ui.label('Отображение: Новые сверху (Max 1000)').classes('text-sm text-gray-400')
        
        # Контейнер для логов с прокруткой
        with ui.scroll_area().classes('w-full h-full bg-gray-900 rounded shadow-inner border border-gray-700'):
            log_container = ui.column().classes('w-full p-2 gap-1')
            
        # Создаем обертку
        reverse_logger = ReverseLog(log_container, max_lines=1000)
        
        # Заполняем историей
        for line in LOG_BUFFER:
            reverse_logger.push(line)
            
        # Регистрируем для получения новых логов
        active_log_elements.append(reverse_logger)
