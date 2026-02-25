import re
from bs4 import BeautifulSoup
from loguru import logger
from sqlmodel import select

from database.models import DelistingEvent, DelistingEventType
from services.web_scraper import WebScraper
from services.article_parser import ArticleParser

class BlogScraperService:
    """
    Сервис для парсинга блогов бирж на предмет анонсов делистинга.
    """

    GATE_DELIST_URL = "https://www.gate.io/announcements/delisted"
    MEXC_DELIST_URL = "https://www.mexc.com/announcements/delistings/spot-18"
    BINANCE_DELIST_URL = "https://www.binance.com/en/support/announcement/delisting?c=161&navId=161"
    KUCOIN_DELIST_URL = "https://www.kucoin.com/announcement/delistings"

    def __init__(self, session_factory, web_scraper: WebScraper, article_parser: ArticleParser):
        self.session_factory = session_factory
        self.web_scraper = web_scraper
        self.article_parser = article_parser

    def _extract_article_links(self, html: str, source: dict) -> dict[str, str]:
        """
        Извлекает и фильтрует ссылки на статьи из HTML.
        Для KuCoin дополнительно ищет ссылки в JSON внутри тегов <script>.
        """
        soup = BeautifulSoup(html, 'html.parser')
        unique_links = {}
        ex_name = source["name"]
        
        # Ищем все ссылки, подходящие под паттерн статьи
        raw_links = soup.find_all('a', href=source["link_pattern"])
        
        for link in raw_links:
            href = link.get('href')
            if not href:
                continue
                
            # Нормализация URL
            if href.startswith('/'):
                full_url = f"{source['domain']}{href}"
            elif href.startswith('http'):
                full_url = href
            else:
                continue
                
            # Очищаем URL от параметров запроса и якорей для точного сравнения
            normalized_url = full_url.rstrip('/')
            base_normalized_url = normalized_url.split('?')[0].split('#')[0]
            base_source_url = source["url"].split('?')[0].split('#')[0]
            
            # Пропускаем саму главную страницу списка, её родительские пути и пагинацию
            if base_source_url.startswith(base_normalized_url) or "/list/" in full_url:
                continue
                
            title = link.get_text(strip=True)
            if full_url not in unique_links and title:
                unique_links[full_url] = title
                
        # Дополнительный поиск для KuCoin: данные часто скрыты в <script> (JSON state)
        if ex_name == "KUCOIN":
            scripts = soup.find_all('script')
            for script in scripts:
                content = script.string
                if not content or '"records":[' not in content:
                    continue
                
                matches = re.finditer(r'\{"id":\d+,"title":"([^"]+)".*?"path":"([^"]+)"', content)
                for match in matches:
                    item_title = match.group(1)
                    item_path = match.group(2)
                    
                    if not item_path.startswith('http'):
                        item_url = f"{source['domain']}/announcement{item_path}"
                    else:
                        item_url = item_path
                        
                    if item_url not in unique_links:
                        unique_links[item_url] = item_title
                        logger.debug(f"[KUCOIN-JS] Found article: {item_title}")
                        
        return unique_links

    async def check_delistings_blog(self) -> int:
        """
        1. Парсит список статей для каждой настроенной биржи.
        2. Ищет ключевые слова "Delist" и др. в заголовках.
        3. Deep Scan: заходит внутрь и ищет пары.
        4. Сохраняет в БД.
        Возвращает количество найденных новых событий.
        """
        
        sources = [
            {
                "name": "GATEIO",
                "url": self.GATE_DELIST_URL,
                "link_pattern": re.compile(r'/announcements/article/'),
                "domain": "https://www.gate.io"
            },
            {
                "name": "MEXC",
                "url": self.MEXC_DELIST_URL,
                "link_pattern": re.compile(r'/(announcements|support)/'), # MEXC links structure varies
                "domain": "https://www.mexc.com"
            },
            {
                "name": "BINANCE",
                "url": self.BINANCE_DELIST_URL,
                "link_pattern": re.compile(r'/announcement/'),
                "domain": "https://www.binance.com"
            },
            {
                "name": "KUCOIN",
                "url": self.KUCOIN_DELIST_URL,
                "link_pattern": re.compile(r'/announcement/'), # KuCoin uses direct links
                "domain": "https://www.kucoin.com"
            }
        ]
        
        logger.info("Запуск проверки делистингов (Deep Scan Mode)...")
        new_events_total = 0
        
        try:
            async with self.session_factory() as session:
                new_events_count = 0
                
                for source in sources:
                    ex_name = source["name"]
                    logger.info(f"Checking {ex_name} at {source['url']}...")
                    
                    try:
                        html = await self.web_scraper.fetch_html(source["url"])
                        unique_links = self._extract_article_links(html, source)
                        
                        logger.info(f"[{ex_name}] Found {len(unique_links)} candidate articles.")
                        
                        for url, title in unique_links.items():
                            # 1. Проверяем заголовок на наличие триггеров
                            title_lower = title.lower()
                            
                            # Проверяем на исключаемые слова (convert, futures)
                            if any(k in title_lower for k in self.article_parser.IGNORE_KEYWORDS):
                                continue
                                
                            # Проверяем на ключевые слова (Delisting или ST)
                            is_relevant = any(k in title_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS) or \
                                          any(k.lower() in title_lower for k in self.article_parser.ST_TRIGGER_KEYWORDS)
                            
                            if not is_relevant:
                                continue 
                                
                            # 1.1 Пропускаем, если этот URL уже был обработан ранее
                            stmt_url = select(DelistingEvent).where(DelistingEvent.announcement_url == url)
                            existing_url = (await session.execute(stmt_url)).first()
                            if existing_url:
                                continue

                            logger.info(f"[{ex_name}] Analyzing article: {title}")
                            
                            # 2. Заходим внутрь (Deep Scan)
                            article_html = await self.web_scraper.fetch_html(url)
                            affected_tokens = self.article_parser.extract_pairs_from_html(article_html, url)
                            
                            if not affected_tokens:
                                logger.warning(f"[{ex_name}] '{title}' - Pairs not found.")
                                continue
                                
                            # 2.1 Определяем тип события для сохранения в БД
                            if any(k in title_lower for k in self.article_parser.DELIST_TRIGGER_KEYWORDS):
                                event_type = DelistingEventType.DELISTING
                            else:
                                event_type = DelistingEventType.ST
                            
                            # 3. Сохраняем найденное
                            for symbol in affected_tokens:
                                stmt = select(DelistingEvent).where(
                                    DelistingEvent.exchange == ex_name,
                                    DelistingEvent.symbol == symbol,
                                    DelistingEvent.announcement_url == url
                                )
                                existing = (await session.execute(stmt)).first()
                                
                                if not existing:
                                    event = DelistingEvent(
                                        exchange=ex_name,
                                        symbol=symbol,
                                        announcement_title=title,
                                        announcement_url=url,
                                        type=event_type
                                    )
                                    session.add(event)
                                    new_events_count += 1
                                    logger.info(f"Found event ({event_type}): {symbol} in {url}")

                    except Exception as ex_err:
                        logger.error(f"Error checking {ex_name}: {ex_err}")
                
                if new_events_count > 0:
                    await session.commit()
                    logger.success(f"Добавлено {new_events_count} новых записей о делистинге.")
                    new_events_total = new_events_count
                
        except Exception as e:
            logger.error(f"Global Scraper Error: {e}")
            
        return new_events_total
