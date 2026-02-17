import pytest
from database.models import RiskLevel

class TestRiskLevel:
    
    def test_priority_order(self):
        """Проверка, что приоритеты рисков выстроены правильно"""
        assert RiskLevel.NORMAL.priority == 0
        assert RiskLevel.CROSS_RISK.priority == 1
        assert RiskLevel.CROSS_DELISTING.priority == 2
        assert RiskLevel.RISK_ZONE.priority == 3
        assert RiskLevel.DELISTING_PLANNED.priority == 4

    def test_priority_comparison(self):
        """Проверка сравнения приоритетов"""
        assert RiskLevel.DELISTING_PLANNED.priority > RiskLevel.RISK_ZONE.priority
        assert RiskLevel.RISK_ZONE.priority > RiskLevel.CROSS_DELISTING.priority
        assert RiskLevel.CROSS_DELISTING.priority > RiskLevel.CROSS_RISK.priority
        assert RiskLevel.CROSS_RISK.priority > RiskLevel.NORMAL.priority

    def test_priority_logic(self):
        """Эмуляция логики обновления риска"""
        current_risk = RiskLevel.NORMAL
        new_risk = RiskLevel.RISK_ZONE
        
        # Риск должен повыситься
        assert new_risk.priority > current_risk.priority
        
        # Риск не должен понизиться автоматически (если пришел более низкий)
        lower_risk = RiskLevel.CROSS_RISK
        assert not (lower_risk.priority > new_risk.priority)
