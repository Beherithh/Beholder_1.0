from datetime import datetime
from typing import Optional
from enum import Enum
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, Integer, ForeignKey

# Enums
class MonitoringStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class RiskLevel(str, Enum):
    NORMAL = "normal"
    CROSS_RISK = "cross_risk"
    CROSS_DELISTING = "cross_delisting"
    RISK_ZONE = "risk_zone"
    DELISTING_PLANNED = "delisting_planned"

    @property
    def priority(self) -> int:
        """Возвращает числовой приоритет для сравнения (0 = низкий, 4 = высокий)."""
        order = [RiskLevel.NORMAL, RiskLevel.CROSS_RISK, RiskLevel.CROSS_DELISTING, RiskLevel.RISK_ZONE, RiskLevel.DELISTING_PLANNED]
        return order.index(self)

class SignalType(str, Enum):
    PRICE_CHANGE = "price_change"
    VOLUME_ALERT = "volume_alert"
    DELISTING_WARNING = "delisting_warning"
    ST_WARNING = "st_warning"
    RANK_WARNING = "rank_warning"

class DelistingEventType(str, Enum):
    DELISTING = "delisting"
    ST = "st"

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
    
    # Метка источника (алиас файла, заданный пользователем)
    source_label: Optional[str] = Field(default=None) 
    
    # Статус мониторинга (Soft Delete)
    monitoring_status: MonitoringStatus = Field(default=MonitoringStatus.ACTIVE)
    
    # Уровень риска (обновляется скрапером)
    risk_level: RiskLevel = Field(default=RiskLevel.NORMAL)
    
    # CoinMarketCap Rank (для фильтрации мусора)
    cmc_rank: Optional[int] = Field(default=None)

    # Связи
    market_data: list["MarketData"] = Relationship(back_populates="pair")

class DelistingEvent(SQLModel, table=True):
    """
    Таблица найденных объявлений о делистинге.
    Служит "базой знаний" о плохих монетах.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    exchange: str = Field(index=True)     # "GATEIO"
    symbol: str = Field(index=True)       # "1SOS" (только базовый символ, без валюты)
    
    announcement_title: str
    announcement_url: str
    type: DelistingEventType = Field(index=True)
    found_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Флаг, что мы уже обработали этот ивент и создали сигнал (чтобы не спамить)
    # Хотя логика может быть динамической (сравнивать с active_pairs), 
    # но можно оставить для истории.
    
class MarketData(SQLModel, table=True):
    """
    Исторические данные (свечи).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    pair_id: int = Field(sa_column=Column(Integer, ForeignKey("monitoredpair.id", ondelete="CASCADE"), index=True))
    
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
    pair_id: Optional[int] = Field(default=None, index=True)
    raw_message: str # Текст сообщения для Telegram
    
    is_sent: bool = Field(default=False) # Отправлено ли в Telegram
    sent_at: Optional[datetime] = Field(default=None) # Время отправки
