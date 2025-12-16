from datetime import datetime
from typing import Optional
from enum import Enum
from sqlmodel import SQLModel, Field, Relationship

# Enums
class MonitoringStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class RiskLevel(str, Enum):
    NORMAL = "normal"
    RISK_ZONE = "risk_zone"
    DELISTING_PLANNED = "delisting_planned"

class SignalType(str, Enum):
    PRICE_CHANGE = "price_change"
    DELISTING_WARNING = "delisting_warning"
    RISK_NEW = "risk_new"

# Models
class AppSettings(SQLModel, table=True):
    """
    Таблица для хранения настроек приложения (Key-Value).
    Пример: "watched_files" -> "['C:/data/file1.txt']"
    """
    key: str = Field(primary_key=True)
    value: str

class MonitoredPair(SQLModel, table=True):
    """
    Основная таблица отслеживаемых пар.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    exchange: str = Field(index=True) # Например: "GATEIO"
    symbol: str = Field(index=True)   # Например: "BTC/USDT"
    
    # Путь к файлу, откуда пришла пара (для синхронизации)
    source_file: str 
    
    # Статус мониторинга (Soft Delete)
    monitoring_status: MonitoringStatus = Field(default=MonitoringStatus.ACTIVE)
    
    # Уровень риска (обновляется скрапером)
    risk_level: RiskLevel = Field(default=RiskLevel.NORMAL)
    
    # Связи
    market_data: list["MarketData"] = Relationship(back_populates="pair")

class DelistingEvent(SQLModel, table=True):
    """
    Таблица найденных объявлений о делистинге.
    Служит "базой знаний" о плохих монетах.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    exchange: str = Field(index=True)     # "GATEIO"
    symbol: str = Field(index=True)       # "1SOS" (Базовая валюта или полный тикер)
    
    announcement_title: str
    announcement_url: str
    found_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Флаг, что мы уже обработали этот ивент и создали сигнал (чтобы не спамить)
    # Хотя логика может быть динамической (сравнивать с active_pairs), 
    # но можно оставить для истории.
    
class MarketData(SQLModel, table=True):
    """
    Исторические данные (свечи).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    pair_id: int = Field(foreign_key="monitoredpair.id")
    
    timestamp: datetime = Field(index=True)
    open: float
    high: float
    low: float
    close: float
    volume: float

    pair: MonitoredPair = Relationship(back_populates="market_data")

class Signal(SQLModel, table=True):
    """
    Сгенерированные сигналы (алерты).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    type: SignalType
    raw_message: str # Текст сообщения для Telegram
    
    is_sent: bool = Field(default=False) # Отправлено ли в Telegram
