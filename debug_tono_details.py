import asyncio
from database.core import get_session
from database.models import MonitoredPair, DelistingEvent
from sqlmodel import select

async def check():
    async with get_session() as session:
        # Check pairs
        pairs = (await session.execute(select(MonitoredPair).where(MonitoredPair.symbol.contains('TONO')))).scalars().all()
        print(f"TONO Pairs found: {len(pairs)}")
        for p in pairs:
            print(f"Pair ID: {p.id}")
            print(f"  Symbol: {p.symbol}")
            print(f"  Label: {p.source_label}")
            print(f"  Risk: {p.risk_level}")
            print(f"  Exchange: {p.exchange}")
        
        # Check events
        events = (await session.execute(select(DelistingEvent).where(DelistingEvent.symbol.contains('TONO')))).scalars().all()
        print(f"\nTONO Events found: {len(events)}")
        for e in events:
            print(f"Event Exchange: {e.exchange}")
            print(f"  Type: {e.type}")
            print(f"  URL: {e.announcement_url}")

if __name__ == "__main__":
    asyncio.run(check())
