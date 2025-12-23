import asyncio
from database.core import get_session
from database.models import Signal
from sqlmodel import select

async def check():
    async with get_session() as session:
        stmt = select(Signal).order_by(Signal.created_at.desc()).limit(10)
        signals = (await session.execute(stmt)).scalars().all()
        print(f"Total signals found: {len(signals)}")
        for s in signals:
            print(f"- [{s.created_at}] {s.raw_message}")

if __name__ == "__main__":
    asyncio.run(check())
