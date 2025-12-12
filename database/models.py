from datetime import datetime
from enum import Enum
from typing import Optional, List
from sqlmodel import Field, SQLModel, Relationship

# --- Описание перечислений (Enums) ---
# Мы используем классы Enum (наследуемся от str), чтобы ограничить возможные варианты значений.
# В базе данных это будет храниться просто как текст ("active", "normal"), 
# но в коде это защищает нас от опечаток и дает подсказки в редакторе.

class MonitoringStatus(str, Enum):
    """
    Статус отслеживания файла.
    Определяет, есть ли пара в ваших списках (файлах) прямо сейчас.
    """
    ACTIVE = "active"       # Пара найдена в файлах, мы за ней следим
    INACTIVE = "inactive"   # Пара исчезла из файлов (Soft Delete - мягкое удаление, чтобы не терять историю)

class RiskLevel(str, Enum):
    """
    Уровень риска (определяется парсером/скрапером).
    Определяет, есть ли угроза делистинга на бирже.
    """
    NORMAL = "normal"               # Всё хорошо
    RISK_ZONE = "risk_zone"         # Биржа пометила как "рискованно"
    DELISTING_PLANNED = "delisting_planned" # Официальное объявление о делистинге

class SignalType(str, Enum):
    """
    Типы сигналов для уведомлений.
    """
    THRESHOLD_VOLATILITY = "threshold_volatility" # Превышение порога изменения цены
    DELISTING_WARNING = "delisting_warning"       # Найдена информация о делистинге
    RISK_WARNING = "risk_warning"                 # Переход в зону риска

# --- Модели Базы Данных (Таблицы) ---

class MonitoredPair(SQLModel, table=True):
    """
    Таблица отслеживаемых пар.
    Хранит информацию о том, какие пары мы мониторим и их текущий статус.
    """
    # id - первичный ключ, создается автоматически базой данных (1, 2, 3...)
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # Индексированные поля для быстрого поиска
    exchange: str = Field(index=True)           # Название биржи (например, "binance")
    symbol: str = Field(index=True)             # Торговая пара (например, "BTC/USDT")
    
    source_file: str  # Путь к файлу, откуда была загружена эта пара
    
    # Статус жизненного цикла (управляется сервисом FileWatcher)
    # По умолчанию ACTIVE. Если удалить строку из файла, станет INACTIVE.
    monitoring_status: MonitoringStatus = Field(default=MonitoringStatus.ACTIVE)
    
    # Рыночный статус (управляется сервисом Scraper)
    # Если парсер найдет инфо о делистинге, статус изменится.
    risk_level: RiskLevel = Field(default=RiskLevel.NORMAL)
    
    # Время создания и обновления записи
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Связь с таблицей свечей (один-ко-многим).
    # Позволяет получить все свечи пары через `pair.market_data`
    market_data: List["MarketData"] = Relationship(back_populates="pair")

class MarketData(SQLModel, table=True):
    """
    Таблица рыночных данных (Свечи).
    Хранит историю цен (Open, High, Low, Close, Volume).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # Внешний ключ (Foreign Key) - ссылка на id в таблице MonitoredPair.
    # Если MonitoredPair имеет id=5, то тут тоже будет 5.
    pair_id: int = Field(foreign_key="monitoredpair.id")
    
    timestamp: datetime = Field(index=True) # Время свечи
    open: float
    high: float
    low: float
    close: float
    volume: float

    # Обратная связь, чтобы от свечи можно было узнать её пару (market_data.pair)
    pair: Optional[MonitoredPair] = Relationship(back_populates="market_data")

class Signal(SQLModel, table=True):
    """
    Таблица сигналов и уведомлений.
    Хранит историю всех сгенерированных алертов.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    type: SignalType        # Тип уведомления (из списка выше)
    raw_message: str        # Текст сообщения
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = None  # Дата отправки в Телеграм. Если None - еще не отправлено.

class AppSettings(SQLModel, table=True):
    """
    Таблица настроек приложения (Key-Value).
    Пример: key="watched_files", value='["c:/data/gate.txt", "c:/data/binance.txt"]'
    """
    key: str = Field(primary_key=True)
    value: str
