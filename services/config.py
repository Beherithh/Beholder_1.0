from typing import Optional, List, Dict
import json
from pydantic import BaseModel
from database.models import AppSettings

class TelegramConfig(BaseModel):
    """DTO для настроек Telegram"""
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    api_id: Optional[str] = None
    api_hash: Optional[str] = None

class AlertConfig(BaseModel):
    """DTO для настроек алертов"""
    # Часовые настройки
    h_pump_period: int = 6
    h_pump_threshold: Optional[float] = None
    h_dump_period: int = 6
    h_dump_threshold: Optional[float] = None
    
    # Дневные настройки
    d_pump_period: int = 24
    d_pump_threshold: Optional[float] = None
    d_dump_period: int = 24
    d_dump_threshold: Optional[float] = None
    
    # Объем
    v_period: int = 30
    v_threshold: Optional[float] = None
    
    # Дедупликация (защита от спама)
    dedup_hours: int = 12

class CMCConfig(BaseModel):
    """DTO для настроек CoinMarketCap"""
    api_key: Optional[str] = None
    rank_threshold: int = 500
    update_interval_days: int = 5

class SchedulerConfig(BaseModel):
    """DTO для настроек планировщика"""
    market_update_interval_hours: int = 1
    scraper_interval_hours: int = 1
    cmc_update_interval_days: int = 5

class ConfigService:
    """
    Централизованный сервис для доступа к настройкам приложения.
    Отвечает за чтение из БД, преобразование типов и дефолтные значения.
    """
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def _get_str(self, session, key: str, default: Optional[str] = None) -> Optional[str]:
        """Приватный хелпер для получения строки"""
        setting = await session.get(AppSettings, key)
        return setting.value if setting and setting.value != 'None' else default

    async def _get_int(self, session, key: str, default: Optional[int] = None) -> Optional[int]:
        """
        Приватный хелпер для получения int.
        Если default=None, вернет None при отсутствии значения.
        Если default=число, вернет это число при отсутствии значения.
        """
        val = await self._get_str(session, key)
        try:
            # int(float(val)) позволяет парсить строки вида "5.0" как 5
            return int(float(val)) if val else default
        except (ValueError, TypeError):
            return default

    async def _get_float(self, session, key: str, default: Optional[float] = None) -> Optional[float]:
        """
        Приватный хелпер для получения float.
        Работает аналогично _get_int.
        """
        val = await self._get_str(session, key)
        try:
            return float(val) if val else default
        except (ValueError, TypeError):
            return default

    async def get_telegram_config(self) -> TelegramConfig:
        async with self.session_factory() as session:
            return TelegramConfig(
                bot_token=await self._get_str(session, "tg_bot_token"),
                chat_id=await self._get_str(session, "tg_chat_id"),
                api_id=await self._get_str(session, "tg_api_id"),
                api_hash=await self._get_str(session, "tg_api_hash"),
            )

    async def get_alert_config(self) -> AlertConfig:
        async with self.session_factory() as session:
            return AlertConfig(
                # Обязательные параметры (с дефолтами)
                h_pump_period=await self._get_int(session, "alert_price_hours_pump_period", default=6),
                h_dump_period=await self._get_int(session, "alert_price_hours_dump_period", default=6),
                d_pump_period=await self._get_int(session, "alert_price_days_pump_period", default=24),
                d_dump_period=await self._get_int(session, "alert_price_days_dump_period", default=24),
                v_period=await self._get_int(session, "alert_volume_days_period", default=30),
                dedup_hours=await self._get_int(session, "alert_dedup_hours", default=12),
                
                # Опциональные параметры (без дефолтов -> None)
                h_pump_threshold=await self._get_float(session, "alert_price_hours_pump_threshold"),
                h_dump_threshold=await self._get_float(session, "alert_price_hours_dump_threshold"),
                d_pump_threshold=await self._get_float(session, "alert_price_days_pump_threshold"),
                d_dump_threshold=await self._get_float(session, "alert_price_days_dump_threshold"),
                v_threshold=await self._get_float(session, "alert_volume_days_threshold"),
            )

    async def get_cmc_config(self) -> CMCConfig:
        async with self.session_factory() as session:
            return CMCConfig(
                api_key=await self._get_str(session, "cmc_api_key"),
                rank_threshold=await self._get_int(session, "cmc_rank_threshold", default=500),
                update_interval_days=await self._get_int(session, "cmc_update_interval_days", default=5)
            )

    async def get_watched_files(self) -> List[Dict[str, str]]:
        """
        Возвращает список файлов для мониторинга.
        Формат: [{"path": "...", "name": "..."}, ...]
        """
        async with self.session_factory() as session:
            val = await self._get_str(session, "watched_files")
            if not val:
                return []
            
            try:
                data = json.loads(val)
                # Миграция старого формата (список строк)
                if data and isinstance(data[0], str):
                    return [{"path": p, "name": f"List {i+1}"} for i, p in enumerate(data)]
                return data
            except json.JSONDecodeError:
                return []

    async def get_scheduler_config(self) -> SchedulerConfig:
        """Возвращает конфиг для планировщика."""
        async with self.session_factory() as session:
            return SchedulerConfig(
                market_update_interval_hours=await self._get_int(session, "update_interval_hours", default=1),
                scraper_interval_hours=await self._get_int(session, "scraper_interval_hours", default=1),
                cmc_update_interval_days=await self._get_int(session, "cmc_update_interval_days", default=5),
            )
