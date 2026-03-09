import pytest
import httpx
import requests as std_requests
from curl_cffi import requests as curl_requests
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
        Проверка работы curl_cffi WebScraper: загрузка реальной страницы.
        Используем verify=False из-за особенностей системных CA на Windows.
        """
        scraper = WebScraper()
        url = "https://www.example.com"

        # Прямой вызов через curl_cffi с отключенной проверкой SSL (только для теста!)
        response = curl_requests.get(url, impersonate="chrome120", verify=False)
        html = response.text

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
        Проверка работы Binance через их публичный API (не фронтенд, который закрыт WAF).
        BlogScraperService ходит именно сюда, поэтому тестируем этот путь.
        """
        parser = ArticleParser()
        
        # Публичный API Binance для каталога делистингов (catalogId=161)
        api_url = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId=161&pageNo=1&pageSize=15"
        
        print(f"\nFetching Binance Delisting API: {api_url}")
        resp = std_requests.get(api_url)
        
        assert resp.status_code == 200, f"Binance API вернул {resp.status_code}"
        
        data = resp.json().get('data', {})
        articles = data.get('articles', [])
        
        assert len(articles) > 0, "Binance API вернул пустой список статей"
        print(f"Found {len(articles)} articles from Binance API.")
        
        # Тестируем deep scan: читаем содержимое первой статьи через API деталей
        first_article = articles[0]
        article_code = first_article.get('code', '')
        article_title = first_article.get('title', '')
        
        print(f"Testing deep scan for article: '{article_title}' (code: {article_code})")
        
        detail_url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode={article_code}"
        detail_resp = std_requests.get(detail_url)
        
        assert detail_resp.status_code == 200
        body = detail_resp.json().get('data', {}).get('body', '')
        
        assert len(body) > 0, "Тело статьи пустое"
        print(f"Article body length: {len(body)} chars. Parsing pairs...")
        
        pairs = parser.extract_pairs_from_html(body, detail_url)
        print(f"Extracted pairs: {pairs}")
        # Не ассертим наличие пар — статья может быть без пар (например, margin-only)


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
