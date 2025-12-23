import asyncio
from database.core import get_session
from database.models import DelistingEvent, MonitoredPair
from sqlmodel import select

async def check():
    async with get_session() as session:
        events = (await session.execute(select(DelistingEvent))).scalars().all()
        print("--- EVENTS ---")
        for e in events:
            print(f"Event: Symbol='{e.symbol}', Exchange='{e.exchange}', Type='{e.type}'")
        
        pairs = (await session.execute(select(MonitoredPair).where(MonitoredPair.monitoring_status == 'active'))).scalars().all()
        print("\n--- ACTIVE PAIRS ---")
        for p in pairs:
            print(f"Pair: Symbol='{p.symbol}', Exchange='{p.exchange}', Risk='{p.risk_level}'")

if __name__ == "__main__":
    asyncio.run(check())
