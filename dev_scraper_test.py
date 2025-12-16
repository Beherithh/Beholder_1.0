import asyncio
print("STARTING SCRIPT...")
from database.core import init_db, get_session
print("IMPORTS DONE")
from services.scraper import ScraperService
from sqlmodel import select, delete
from database.models import DelistingEvent, Signal, MonitoredPair

async def test_scraper():
    print("1. Инициализация (создание новых таблиц)...")
    await init_db()
    
    # Очистка для чистого теста
    async with get_session() as session:
        await session.execute(delete(DelistingEvent))
        await session.execute(delete(Signal))
        await session.commit()

    print("2. Запуск скрапера...")
    service = ScraperService(get_session)
    await service.check_delistings_blog()
    
    print("3. Проверка результатов...")
    async with get_session() as session:
        events = await session.execute(select(DelistingEvent))
        all_events = events.scalars().all()
        print(f"Найдено событий делистинга: {len(all_events)}")
        for e in all_events[:5]:
            print(f" - {e.symbol}: {e.announcement_title[:50]}...")
            
        signals = await session.execute(select(Signal))
        all_signals = signals.scalars().all()
        print(f"Сгенерировано сигналов: {len(all_signals)}")
        for s in all_signals:
            print(f" [SIGNAL] {s.raw_message}")

if __name__ == "__main__":
    asyncio.run(test_scraper())
