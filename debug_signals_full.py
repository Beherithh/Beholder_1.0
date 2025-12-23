import asyncio
from database.core import get_session
from database.models import Signal
from sqlmodel import select

async def check():
    async with get_session() as session:
        stmt = select(Signal).order_by(Signal.created_at.desc()).limit(10)
        signals = (await session.execute(stmt)).scalars().all()
        print(f"Total signals found: {len(signals)}")
        for i, s in enumerate(signals):
            print(f"{i+1}. [{s.created_at}]")
            print(f"   {s.raw_message}\n")

if __name__ == "__main__":
    asyncio.run(check())
