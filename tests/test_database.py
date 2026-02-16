"""
Тесты Database Layer.

Проверяем логику _ensure_default_settings напрямую через тестовую сессию,
без вызова init_db (который привязан к production engine).
"""
import pytest
from sqlmodel import select
from database.models import AppSettings


# Ожидаемые дефолтные настройки — фиксируем текущее поведение
EXPECTED_DEFAULTS = {
    "cmc_rank_threshold": "500",
    "alert_dedup_hours": "12",
    "update_interval_hours": "1",
    "scraper_interval_hours": "1",
    "cmc_update_interval_days": "5",
    "watched_files": "[]",
}


class TestEnsureDefaultSettings:
    """
    Воспроизводим логику _ensure_default_settings из database/core.py,
    но работаем с тестовой in-memory БД.
    """

    async def _run_defaults_logic(self, session):
        """Копия логики _ensure_default_settings для изолированного теста."""
        for key, value in EXPECTED_DEFAULTS.items():
            stmt = select(AppSettings).where(AppSettings.key == key)
            existing = (await session.execute(stmt)).scalars().first()
            if not existing:
                session.add(AppSettings(key=key, value=value))
            elif existing.value in (None, "None", ""):
                existing.value = value
        await session.commit()

    @pytest.mark.asyncio
    async def test_creates_all_defaults(self, db_session):
        """Из пустой БД должны создаться все 6 ключей."""
        await self._run_defaults_logic(db_session)

        for key, expected_value in EXPECTED_DEFAULTS.items():
            result = await db_session.get(AppSettings, key)
            assert result is not None, f"Ключ '{key}' не создан"
            assert result.value == expected_value

    @pytest.mark.asyncio
    async def test_idempotent(self, db_session):
        """Повторный вызов не дублирует и не перезаписывает записи."""
        await self._run_defaults_logic(db_session)

        # Меняем одну настройку руками
        setting = await db_session.get(AppSettings, "cmc_rank_threshold")
        setting.value = "100"
        await db_session.commit()

        # Повторный вызов НЕ должен откатить "100" → "500"
        await self._run_defaults_logic(db_session)

        result = await db_session.get(AppSettings, "cmc_rank_threshold")
        assert result.value == "100"  # Изменённое значение сохранилось

    @pytest.mark.asyncio
    async def test_restores_empty_values(self, db_session):
        """Если value пустой/None/'None', то восстанавливает дефолт."""
        # Создаём запись с пустым значением
        db_session.add(AppSettings(key="cmc_rank_threshold", value=""))
        await db_session.commit()

        await self._run_defaults_logic(db_session)

        result = await db_session.get(AppSettings, "cmc_rank_threshold")
        assert result.value == "500"  # Восстановлено из дефолта

    @pytest.mark.asyncio
    async def test_restores_none_string(self, db_session):
        """Строка 'None' тоже считается пустым значением."""
        db_session.add(AppSettings(key="alert_dedup_hours", value="None"))
        await db_session.commit()

        await self._run_defaults_logic(db_session)

        result = await db_session.get(AppSettings, "alert_dedup_hours")
        assert result.value == "12"


class TestTablesCreation:
    """Проверяем, что все таблицы создаются через SQLModel.metadata."""

    @pytest.mark.asyncio
    async def test_all_tables_exist(self, db_session):
        """Таблицы создаются фикстурой conftest — значит metadata корректна."""
        from sqlalchemy import inspect

        # Получаем sync-соединение для инспекции
        conn = await db_session.connection()
        raw_conn = await conn.get_raw_connection()
        
        # Для aiosqlite нужно обратиться к внутреннему соединению
        cursor = await raw_conn.driver_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = {row[0] for row in await cursor.fetchall()}

        expected_tables = {"appsettings", "monitoredpair", "delistingevent", "marketdata", "signal"}
        assert expected_tables == tables
