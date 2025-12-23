from database.models import RiskLevel

def test():
    r = RiskLevel.NORMAL
    print(f"NORMAL priority: {r.priority}")
    r2 = RiskLevel.DELISTING_PLANNED
    print(f"DELISTING_PLANNED priority: {r2.priority}")
    
    # Try comparison
    print(f"Is DELISTING > NORMAL? {r2.priority > r.priority}")

if __name__ == "__main__":
    test()
