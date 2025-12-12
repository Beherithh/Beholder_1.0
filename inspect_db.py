import asyncio
from sqlmodel import select
from database.core import get_session
from database.models import MonitoredPair, MonitoringStatus

async def main():
    async with get_session() as session:
        result = await session.execute(select(MonitoredPair))
        pairs = result.scalars().all()
        print(f"Total pairs in DB: {len(pairs)}")
        for p in pairs:
            print(f"- {p.exchange}: {p.symbol} ({p.monitoring_status}) [{p.source_file}]")

if __name__ == "__main__":
    asyncio.run(main())
