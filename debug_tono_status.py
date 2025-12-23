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
            print(f"- ID: {p.id}, Symbol: {p.symbol}, Label: {p.source_label}, Risk: {p.risk_level}")
        
        # Check events
        events = (await session.execute(select(DelistingEvent).where(DelistingEvent.symbol.contains('TONO')))).scalars().all()
        print(f"\nTONO Events found: {len(events)}")
        for e in events:
            print(f"- Exchange: {e.exchange}, Type: {e.type}, URL: {e.announcement_url}")

if __name__ == "__main__":
    asyncio.run(check())
