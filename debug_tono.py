import asyncio
from database.core import get_session
from database.models import DelistingEvent
from sqlmodel import select

async def check():
    async with get_session() as session:
        # Check specific URL
        stmt = select(DelistingEvent).where(DelistingEvent.announcement_url.contains('17827791532243'))
        events = (await session.execute(stmt)).scalars().all()
        print(f"Events for TONO article: {len(events)}")
        for e in events:
            print(f"- Symbol: '{e.symbol}', Type: {e.type}")

if __name__ == "__main__":
    asyncio.run(check())
