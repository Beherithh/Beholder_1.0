"""
Тесты FileWatcherService.

Покрываем:
  1. _read_files — парсинг JSON, нормализация символов, маппинг бирж
  2. sync_files — добавление, архивирование (soft delete), реактивация
"""
import json
import pytest
from pathlib import Path
from sqlmodel import select

from database.models import MonitoredPair, MonitoringStatus, RiskLevel
from services.file_watcher import FileWatcherService


# ==================== Helpers ====================

def create_json_file(tmp_path: Path, filename: str, symbols: list[str]) -> Path:
    """
    Создаёт JSON-файл в формате, ожидаемом FileWatcher.
    Формат: {"listHelper": [{"symbol": "BTCUSDT"}, ...]}
    """
    data = {"listHelper": [{"symbol": s} for s in symbols]}
    file_path = tmp_path / filename
    file_path.write_text(json.dumps(data), encoding="utf-8")
    return file_path


# ==================== _read_files Tests ====================

class TestReadFiles:
    """Тесты парсинга файлов и нормализации символов."""

    @pytest.mark.asyncio
    async def test_basic_parsing(self, tmp_path):
        """Файл Gate_instruments_USDT.json → exchange=GATEIO, symbol=BTC/USDT."""
        fp = create_json_file(tmp_path, "Gate_instruments_USDT.json", ["BTCUSDT"])
        service = FileWatcherService(session_factory=None)  # _read_files не использует БД

        result = await service._read_files([{"path": str(fp), "name": "Gate 1"}])

        assert len(result) == 1
        exchange, symbol, path, label = next(iter(result))
        assert exchange == "GATEIO"       # GATE → GATEIO через EXCHANGE_MAPPING
        assert symbol == "BTC/USDT"       # BTCUSDT → BTC/USDT
        assert label == "Gate 1"

    @pytest.mark.asyncio
    async def test_symbol_with_separator(self, tmp_path):
        """Символ с разделителем '_' нормализуется в '/'."""
        fp = create_json_file(tmp_path, "Gate_instruments_USDT.json", ["BTC_USDT"])
        service = FileWatcherService(session_factory=None)

        result = await service._read_files([{"path": str(fp), "name": "Test"}])
        _, symbol, _, _ = next(iter(result))
        assert symbol == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_multiple_symbols(self, tmp_path):
        """Несколько символов в одном файле."""
        fp = create_json_file(
            tmp_path, "Gate_instruments_USDT.json",
            ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        )
        service = FileWatcherService(session_factory=None)

        result = await service._read_files([{"path": str(fp), "name": "Test"}])
        symbols = {sym for _, sym, _, _ in result}
        assert symbols == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}

    @pytest.mark.asyncio
    async def test_missing_file(self, tmp_path):
        """Несуществующий файл пропускается без ошибки."""
        service = FileWatcherService(session_factory=None)

        result = await service._read_files([{"path": "C:/nonexistent.json", "name": "X"}])
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_empty_listhelper(self, tmp_path):
        """Файл без ключа listHelper — пустой результат."""
        fp = tmp_path / "Gate_instruments_USDT.json"
        fp.write_text('{"other_key": []}', encoding="utf-8")
        service = FileWatcherService(session_factory=None)

        result = await service._read_files([{"path": str(fp), "name": "Test"}])
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_filename_with_number_prefix(self, tmp_path):
        """Формат '2_Gate_instruments_USDT.json' — число-префикс игнорируется."""
        fp = create_json_file(tmp_path, "2_Gate_instruments_USDT.json", ["ADAUSDT"])
        service = FileWatcherService(session_factory=None)

        result = await service._read_files([{"path": str(fp), "name": "Test"}])
        exchange, symbol, _, _ = next(iter(result))
        assert exchange == "GATEIO"
        assert symbol == "ADA/USDT"


# ==================== sync_files Tests ====================

class TestSyncFiles:
    """Тесты синхронизации: add, archive (soft delete), reactivate."""

    @pytest.mark.asyncio
    async def test_add_new_pairs(self, tmp_path, session_factory, db_session):
        """Из пустой БД — все пары добавляются как ACTIVE."""
        fp = create_json_file(tmp_path, "Gate_instruments_USDT.json", ["BTCUSDT", "ETHUSDT"])
        service = FileWatcherService(session_factory=session_factory)

        stats = await service.sync_files([{"path": str(fp), "name": "Gate 1"}])

        assert stats["added"] == 2
        assert stats["archived"] == 0

        # Проверяем в БД
        result = await db_session.execute(select(MonitoredPair))
        pairs = result.scalars().all()
        assert len(pairs) == 2
        assert all(p.monitoring_status == MonitoringStatus.ACTIVE for p in pairs)

    @pytest.mark.asyncio
    async def test_archive_removed_pairs(self, tmp_path, session_factory, db_session):
        """Пара исчезла из файла → статус INACTIVE (soft delete)."""
        # Шаг 1: добавляем 2 пары
        fp = create_json_file(tmp_path, "Gate_instruments_USDT.json", ["BTCUSDT", "ETHUSDT"])
        service = FileWatcherService(session_factory=session_factory)
        await service.sync_files([{"path": str(fp), "name": "Gate 1"}])

        # Шаг 2: оставляем только BTC
        fp2 = create_json_file(tmp_path, "Gate_instruments_USDT.json", ["BTCUSDT"])
        stats = await service.sync_files([{"path": str(fp2), "name": "Gate 1"}])

        assert stats["archived"] == 1  # ETH заархивирована

        # Проверяем: ETH стала INACTIVE
        result = await db_session.execute(
            select(MonitoredPair).where(MonitoredPair.symbol == "ETH/USDT")
        )
        eth = result.scalars().first()
        assert eth.monitoring_status == MonitoringStatus.INACTIVE

    @pytest.mark.asyncio
    async def test_reactivate_pair(self, tmp_path, session_factory, db_session):
        """Пара вернулась в файл → статус снова ACTIVE."""
        fp = create_json_file(tmp_path, "Gate_instruments_USDT.json", ["BTCUSDT", "ETHUSDT"])
        service = FileWatcherService(session_factory=session_factory)

        # Шаг 1: добавляем
        await service.sync_files([{"path": str(fp), "name": "Gate 1"}])
        # Шаг 2: убираем ETH
        fp2 = create_json_file(tmp_path, "Gate_instruments_USDT.json", ["BTCUSDT"])
        await service.sync_files([{"path": str(fp2), "name": "Gate 1"}])
        # Шаг 3: возвращаем ETH
        fp3 = create_json_file(tmp_path, "Gate_instruments_USDT.json", ["BTCUSDT", "ETHUSDT"])
        stats = await service.sync_files([{"path": str(fp3), "name": "Gate 1"}])

        assert stats["reactivated"] == 1

        result = await db_session.execute(
            select(MonitoredPair).where(MonitoredPair.symbol == "ETH/USDT")
        )
        eth = result.scalars().first()
        assert eth.monitoring_status == MonitoringStatus.ACTIVE
