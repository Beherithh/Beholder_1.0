import asyncio
import time
from loguru import logger
import undetected_chromedriver as uc
from undetected_chromedriver import ChromeOptions

class WebScraper:
    """
    Сервис для получения HTML-кода страниц, защищенных от ботов (Cloudflare и т.д.).
    Использует undetected-chromedriver для обхода защиты на серверах.
    """

    async def fetch_html(self, url: str) -> str:
        """
        Загружает страницу через undetected Selenium и возвращает её HTML.
        Запускается в отдельном потоке, чтобы не блокировать event loop.
        """
        logger.info(f"Запуск Chrome через undetected-chromedriver для {url}...")
        
        # Запускаем синхронную функцию в executor'е
        html = await asyncio.get_running_loop().run_in_executor(None, self._selenium_get, url)
        
        if not html:
            raise ValueError(f"Selenium вернул пустой HTML для {url}")
        return html

    def _selenium_get(self, url: str) -> str:
        """
        Синхронная внутренняя функция для работы с драйвером.
        """
        options = ChromeOptions()
        # Для сервера обязательно нужен headless режим.
        # undetected-chromedriver требует специального подхода для headless.
        options.add_argument("--headless=new") 
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        
        driver = None
        try:
            # Инициализация undetected драйвера
            # version_main указывает основную версию Chrome (обычно подхватывает сам)
            driver = uc.Chrome(options=options)
            
            driver.get(url)
            # Даем время на выполнение JS и автоматическое прохождение Cloudflare challenge
            time.sleep(10) 
            return driver.page_source
        except Exception as e:
            logger.error(f"Selenium Error fetching {url}: {e}")
            return ""
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
