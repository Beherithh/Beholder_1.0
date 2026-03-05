import asyncio
from loguru import logger
from curl_cffi import requests

class WebScraper:
    """
    Сервис для получения HTML-кода страниц, защищенных от ботов (Cloudflare и т.д.).
    Использует curl_cffi для подмены TLS Fingerprint, не требует локального Chrome.
    Предыдущая версия использовала Selenium с undetected-chromedriver, была проблема с блокировками на Gate.io
    """

    async def fetch_html(self, url: str) -> str:
        """
        Загружает страницу через curl_cffi с отпечатком современного Chrome.
        """
        logger.info(f"Скрапинг через curl_cffi (impersonate=chrome120) для {url}...")
        
        # Для curl_cffi лучше делать запросы в отдельном потоке, 
        # хотя у них есть и AsyncSession, executor надежнее с локами
        html = await asyncio.get_running_loop().run_in_executor(None, self._curl_get, url)
        
        if not html:
            logger.warning(f"curl_cffi вернул пустой HTML для {url}")
        return html

    def _curl_get(self, url: str) -> str:
        """
        Синхронная внутренняя функция для работы с requests.
        """
        try:
            # используем impersonate='chrome120', что обманывает Cloudflare TLS
            response = requests.get(url, impersonate="chrome120", timeout=15)
            
            # Проверяем на базовую ошибку
            if response.status_code != 200:
                logger.warning(f"HTTP {response.status_code} при загрузке {url}")
                
            return response.text
        except Exception as e:
            logger.error(f"curl_cffi Error fetching {url}: {e}")
            return ""
