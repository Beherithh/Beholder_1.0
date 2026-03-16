import os
import sys
import logging
from loguru import logger


class InterceptHandler(logging.Handler):
    """
    Перехватчик стандартного logging → Loguru.
    Uvicorn, Starlette, NiceGUI используют стандартный logging,
    который без этого обработчика НЕ попадает в наши Loguru файлы.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Находим соответствующий уровень Loguru
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Ищем реальный источник вызова, минуя стек logging
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# Флаг, чтобы избежать повторной инициализации
is_initialized = False

def init_logging():
    """
    Централизованная настройка всех обработчиков Loguru для проекта.
    Включает логирование в консоль, в файл и в UI.
    """
    global is_initialized
    if is_initialized:
        return

    # 1. Удаляем стандартный обработчик и начинаем с чистого листа
    logger.remove()

    # 1.5. Принудительно задаем UTF-8 для стандартных потоков (полезно для Windows)
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

    # 2. Обработчик для вывода в КОНСОЛЬ
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True
    )

    # 3. Обработчик для записи в ФАЙЛ с ротацией
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    logger.add(
        os.path.join(log_dir, "beholder.log"),
        rotation="10 MB",
        retention="10 days",
        compression="zip",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}:{function}:{line} - {message}",
        encoding="utf-8"
    )

    # 4. Перехватываем стандартный logging (Uvicorn, Starlette, NiceGUI)
    # Без этого сообщения "NiceGUI ready to go" не попадают в файл при запуске как служба
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)
    # Явно указываем логгеры Uvicorn (на случай если они добавляют свои хендлеры)
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_log = logging.getLogger(uvicorn_logger_name)
        uv_log.handlers = [InterceptHandler()]
        uv_log.propagate = False

    # 5. Обработчики для UI (импортируем их здесь, чтобы избежать циклических зависимостей)
    from ui.pages.logs import broadcast_log
    from ui.pages.errors import broadcast_error_log
    from ui.pages.warnings import broadcast_warning_log

    # 5.1. Общий лог для UI
    logger.add(broadcast_log, format="{message}", level="INFO")
    
    # 5.2. Лог ошибок для UI
    logger.add(broadcast_error_log, format="{message}", level="ERROR")
    
    # 5.3. Лог предупреждений для UI (фильтруем, чтобы не дублировать ошибки)
    logger.add(broadcast_warning_log, format="{message}", level="WARNING", filter=lambda r: r["level"].name == "WARNING")

    is_initialized = True
    logger.info("Logging system initialized.")
