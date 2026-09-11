"""
Тесты моделей данных и вычисляемых свойств (database.models).

Проверяют:
1. Вычисляемое свойство MonitoredPair.base_currency:
   - Корректное извлечение тикера монеты при разных форматах записи ('BTC/USDT', 'ETH_USDT', 'DOGE').
   - Приведение к верхнему регистру ('sol/usdt' -> 'SOL').
   - Поддержку токенов с числовыми префиксами ('1000PEPE/USDT' -> '1000PEPE').
2. Вычисляемое свойство MonitoredPair.labels_display:
   - Распаковку JSON-списка меток ('["Gate 1", "Mexc 2"]' -> 'Gate 1, Mexc 2').
   - Обработку одиночной строки без JSON.
   - Обработку None ('Unknown').
   - Безопасный fallback при повреждённом JSON.
3. Иерархию приоритетов RiskLevel:
   - Соблюдение строгого порядка эскалации рисков.
"""

import pytest
from database.models import MonitoredPair, RiskLevel, MarketType


class TestMonitoredPairProperties:
    """Тестирование бизнес-логики вычисляемых свойств MonitoredPair."""

    @pytest.mark.parametrize("symbol, expected_base", [
        ("BTC/USDT", "BTC"),
        ("ETH_USDT", "ETH"),
        ("SOL", "SOL"),
        ("1000LUNC/USDT", "1000LUNC"),
        ("doge/usdt", "DOGE"),
        ("pepe_btc", "PEPE"),
    ])
    def test_base_currency_extraction(self, symbol: str, expected_base: str) -> None:
        """Извлечение базовой валюты из различных форматов тикеров."""
        pair = MonitoredPair(
            exchange="BINANCE",
            symbol=symbol,
            market_type=MarketType.SPOT,
            source_file="test.json",
        )
        assert pair.base_currency == expected_base

    @pytest.mark.parametrize("source_label, expected_display", [
        ('["Gate 1", "Mexc 2"]', "Gate 1, Mexc 2"),
        ('["Single Label"]', "Single Label"),
        ("Direct Text Label", "Direct Text Label"),
        (None, "Unknown"),
        ("", "Unknown"),
        ("[corrupted json list", "[corrupted json list"),
    ])
    def test_labels_display_formatting(self, source_label: str | None, expected_display: str) -> None:
        """Форматирование меток для отображения в интерфейсе пользователя."""
        pair = MonitoredPair(
            exchange="GATEIO",
            symbol="BTC/USDT",
            source_file="test.json",
            source_label=source_label,
        )
        assert pair.labels_display == expected_display


class TestRiskLevelHierarchy:
    """Проверка иерархии уровней риска."""

    def test_strict_priority_escalation(self) -> None:
        """
        Приоритеты рисков обязаны строго возрастать:
        NORMAL (0) < CROSS_RISK (1) < CROSS_DELISTING (2) < RISK_ZONE (3) < DELISTING_PLANNED (4).
        """
        levels = [
            RiskLevel.NORMAL,
            RiskLevel.CROSS_RISK,
            RiskLevel.CROSS_DELISTING,
            RiskLevel.RISK_ZONE,
            RiskLevel.DELISTING_PLANNED,
        ]

        priorities = [lvl.priority for lvl in levels]
        assert priorities == sorted(priorities)
        assert len(priorities) == len(set(priorities)), "Приоритеты каждого уровня риска обязаны быть уникальными"
