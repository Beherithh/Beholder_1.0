import asyncio
from database.core import get_session
from services.scraper import ScraperService

async def test_scrape():
    scraper = ScraperService(get_session)
    url = "https://www.mexc.com/announcements/article/delisting-of-tono-usdc-trading-pair-17827791532243"
    tokens = await scraper._extract_pairs_from_article(url)
    print(f"Extracted tokens: {tokens}")

if __name__ == "__main__":
    asyncio.run(test_scrape())
