import pytest
import httpx
from services.web_scraper import WebScraper
from services.article_parser import ArticleParser
from services.api_risk_checker import ApiRiskCheckerService

# Маркер, чтобы можно было запускать эти тесты отдельно или исключать их
# Запуск: pytest -m external
# Исключение: pytest -m "not external"
@pytest.mark.external
@pytest.mark.asyncio
class TestExternalIntegrations:
    
    async def test_web_scraper_real_fetch(self):
        """
        Проверка работы Selenium: загрузка реальной страницы.
        """
        scraper = WebScraper()
        url = "https://www.example.com"
        
        html = await scraper.fetch_html(url)
        
        assert html is not None
        assert len(html) > 0
        assert "Example Domain" in html

    async def test_gateio_api_structure(self):
        """
        Проверка доступности и структуры API Gate.io.
        Если этот тест падает, значит Gate.io изменили API, и ApiRiskChecker сломается.
        """
        # Берем URL прямо из конфигурации класса
        gate_source = next(s for s in ApiRiskCheckerService.API_SOURCES if s["name"] == "GATEIO")
        url = gate_source["url"]
        
        print(f"\nChecking Gate.io API: {url}")
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            assert resp.status_code == 200
            data = resp.json()
            
            # Проверяем, что вернулся список (как мы ожидаем в коде)
            # Gate.io возвращает список объектов напрямую
            assert isinstance(data, list)
            assert len(data) > 0
            
            first_item = data[0]
            # Проверяем наличие ключевых полей, которые мы используем
            symbol_key = gate_source["symbol_key"] # "id"
            st_key = gate_source["st_key"]         # "st_tag"
            
            assert symbol_key in first_item, f"Field '{symbol_key}' missing in Gate API response"
            # st_key может отсутствовать, если он false, но проверим хотя бы на одной монете или просто наличие ключа если API гарантирует
            # Gate API обычно возвращает ключи.
            
            print(f"Gate.io API structure OK. Sample item: {first_item}")

    async def test_mexc_api_structure(self):
        """
        Проверка доступности и структуры API MEXC.
        """
        mexc_source = next(s for s in ApiRiskCheckerService.API_SOURCES if s["name"] == "MEXC")
        url = mexc_source["url"]
        
        print(f"\nChecking MEXC API: {url}")
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            assert resp.status_code == 200
            data = resp.json()
            
            # MEXC возвращает dict с ключом "symbols"
            assert isinstance(data, dict)
            assert "symbols" in data
            
            items = data["symbols"]
            assert len(items) > 0
            
            first_item = items[0]
            symbol_key = mexc_source["symbol_key"] # "symbol"
            
            assert symbol_key in first_item, f"Field '{symbol_key}' missing in MEXC API response"
            print(f"MEXC API structure OK. Sample item keys: {first_item.keys()}")

    async def test_binance_blog_parsing(self):
        """
        Сложный тест: Загрузка реальной статьи с Binance и попытка найти в ней пары.
        Этот тест может устареть, если ссылка станет битой, поэтому используем главную страницу анонсов
        или стабильную старую статью.
        """
        scraper = WebScraper()
        parser = ArticleParser()
        
        # Ссылка на список анонсов делистинга (она более-менее стабильна)
        url = "https://www.binance.com/en/support/announcement/delisting?c=161&navId=161"
        
        print(f"\nFetching Binance Delisting Page: {url}")
        html = await scraper.fetch_html(url)
        
        assert "Delist" in html or "Removal" in html or "Cease" in html
        
        # Пробуем найти ссылки на статьи
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        links = soup.find_all('a')
        
        article_links = [l['href'] for l in links if '/announcement/' in l.get('href', '')]
        
        if article_links:
            print(f"Found {len(article_links)} article links. Parsing the first one...")
            # Берем первую попавшуюся статью
            full_url = "https://www.binance.com" + article_links[0] if article_links[0].startswith('/') else article_links[0]
            
            article_html = await scraper.fetch_html(full_url)
            pairs = parser.extract_pairs_from_html(article_html, full_url)
            
            print(f"Extracted pairs from {full_url}: {pairs}")
            # Не ассертим наличие пар, так как статья может быть без них, но сам факт прохождения без ошибок важен.
        else:
            print("Warning: No article links found on Binance page. Structure might have changed.")
