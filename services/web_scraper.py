import asyncio
import time
from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

class WebScraper:
    """
    Сервис для получения HTML-кода страниц, защищенных от ботов (Cloudflare и т.д.).
    Использует Selenium в headless-режиме.
    """

    async def fetch_html(self, url: str) -> str:
        """
        Загружает страницу через Selenium и возвращает её HTML.
        Запускается в отдельном потоке, чтобы не блокировать event loop.
        """
        logger.info(f"Запуск Chrome через Selenium для {url}...")
        
        # Запускаем синхронную функцию в executor'е
        html = await asyncio.get_running_loop().run_in_executor(None, self._selenium_get, url)
        
        if not html:
            raise ValueError(f"Selenium вернул пустой HTML для {url}")
        return html

    def _selenium_get(self, url: str) -> str:
        """
        Синхронная внутренняя функция для работы с драйвером.
        """
        options = Options()
        options.add_argument("--headless=new") # Запуск без окна
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        driver = None
        try:
            # Инициализация драйвера (автоматически скачает нужную версию)
            driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
            
            # Скрытие флага автоматизации в браузере
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": "const newProto = navigator.__proto__; delete newProto.webdriver; navigator.__proto__ = newProto;"
            })

            driver.get(url)
            # Даем время на выполнение JS и автоматическое прохождение Cloudflare challenge
            time.sleep(10) 
            return driver.page_source
        except Exception as e:
            logger.error(f"Selenium Error fetching {url}: {e}")
            return ""
        finally:
            if driver:
                driver.quit()
