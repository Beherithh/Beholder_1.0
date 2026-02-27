import pytest
from unittest.mock import MagicMock, PropertyMock
from ui.pages.logs import LOG_BUFFER, MAX_LOG_LINES, broadcast_log, FilteredLogViewer
import ui.pages.logs as logs_module
import ui.pages.warnings as warnings_module
import ui.pages.errors as errors_module

@pytest.fixture(autouse=True)
def clear_log_state():
    """Очищает глобальные буферы и списки подписчиков перед каждым тестом."""
    logs_module.LOG_BUFFER.clear()
    logs_module.active_log_elements.clear()
    warnings_module.warning_log_elements.clear()
    errors_module.error_log_elements.clear()
    yield

def test_log_buffer_limit():
    """Проверка ограничения размера глобального буфера."""
    for i in range(MAX_LOG_LINES + 10):
        broadcast_log(f"Message {i}")
    
    assert len(logs_module.LOG_BUFFER) == MAX_LOG_LINES
    assert logs_module.LOG_BUFFER[0] == "Message 10"
    assert logs_module.LOG_BUFFER[-1] == f"Message {MAX_LOG_LINES + 9}"

def test_filtered_log_viewer_levels():
    """Проверка фильтрации уровней в FilteredLogViewer."""
    mock_container = MagicMock()
    # Создаем вьювер только для ошибок
    viewer = FilteredLogViewer(mock_container, levels=['ERROR', 'CRITICAL'])
    
    # Эти должны пройти
    assert viewer._should_show_log("[12:00:00] ERROR: System failure") is True
    assert viewer._should_show_log("[12:00:00] CRITICAL: Boom") is True
    
    # Эти должны отсеяться
    assert viewer._should_show_log("[12:00:00] INFO: All good") is False
    assert viewer._should_show_log("[12:00:00] WARNING: Low disk space") is False

def test_filtered_log_viewer_push():
    """Проверка добавления лога и обновления счетчиков в UI."""
    mock_container = MagicMock()
    mock_counter = MagicMock()
    
    viewer = FilteredLogViewer(mock_container, levels=['WARNING'])
    viewer.set_counter('WARNING', mock_counter)
    
    # Имитируем добавление лога
    viewer.push("[12:00:00] WARNING: Test warning")
    
    assert viewer.counts['WARNING'] == 1
    assert mock_counter.text == 'WARNING: 1'
    assert len(viewer.labels) == 1

def test_warning_broadcast_routing():
    """Проверка, что предупреждения уходят в правильный список рассылки."""
    mock_element = MagicMock()
    mock_element.client.has_socket_connection = True
    warnings_module.warning_log_elements.append(mock_element)
    
    # Имитируем запись лога через Loguru record
    mock_record = MagicMock()
    # Чтобы замокать атрибут .name, который зарезервирован в MagicMock, 
    # используем PropertyMock или простое присваивание после создания
    level_mock = MagicMock()
    type(level_mock).name = PropertyMock(return_value="WARNING")
    
    mock_record.record = {
        "time": MagicMock(strftime=lambda x: "12:00:00"),
        "level": level_mock,
        "message": "Careful!"
    }
    
    warnings_module.broadcast_warning_log(mock_record)
    
    # Проверяем, что push был вызван у элемента на странице Warnings
    mock_element.push.assert_called_once_with("[12:00:00] WARNING: Careful!")

def test_error_broadcast_routing():
    """Проверка, что ошибки уходят в правильный список рассылки."""
    mock_element = MagicMock()
    mock_element.client.has_socket_connection = True
    errors_module.error_log_elements.append(mock_element)
    
    mock_record = MagicMock()
    level_mock = MagicMock()
    type(level_mock).name = PropertyMock(return_value="ERROR")
    
    mock_record.record = {
        "time": MagicMock(strftime=lambda x: "12:00:00"),
        "level": level_mock,
        "message": "Broken!"
    }
    
    errors_module.broadcast_error_log(mock_record)
    
    mock_element.push.assert_called_once_with("[12:00:00] ERROR: Broken!")
