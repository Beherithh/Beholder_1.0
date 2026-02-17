import re
from typing import Set
from bs4 import BeautifulSoup
from loguru import logger

class ArticleParser:
    """
    Сервис для анализа текста статей и извлечения из них торговых пар.
    """
    
    DELIST_TRIGGER_KEYWORDS = {"delist", "oppos", "remov", "offline", "risk", "suspend"}
    ST_TRIGGER_KEYWORDS = {"st_tag", "ST Warning", "Assessment Zone", "Monitoring Tag"}
    IGNORE_KEYWORDS = {"convert", "future", "perpetual", "option", 'margin'}
    
    QUOTE_CURRENCIES = ("USDT", "BTC", "ETH", "BUSD", "BNB", "SOL", "USDC")
    
    # Регулярка для поиска ПАР в тексте (например: "ABC_USDT", "ABC/ETH", "ABCUSDT", "ICE")
    PAIR_PATTERN = re.compile(
        r'\b(?![0-9]+\b)([A-Z0-9]{2,11})[-_/\.]?(USDT|BTC|ETH|BUSD|BNB|SOL|USDC)?\b'
    )

    def extract_pairs_from_html(self, html: str, url: str) -> Set[str]:
        """
        Парсит HTML статьи, очищает от шума и ищет торговые пары.
        Возвращает набор найденных БАЗОВЫХ валют.
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # --- Cleaning DOM from Noise ---
            # 1. Remove standard non-content tags
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
                tag.decompose()

            # 2. Heuristics for sidebars/related via keywords in class/id
            noise_pattern = re.compile(
                r'(related|sidebar|menu|widget|recent|popular|recommend|footer|header|cookie|social|share|comment|banner|ad-|promo|breadcrumb|nav|pagenavi)', 
                re.I
            )
            
            for tag in list(soup.find_all(attrs={"class": noise_pattern})):
                tag.decompose()
                
            for tag in list(soup.find_all(attrs={"id": noise_pattern})):
                tag.decompose()

            # 3. Targeted extraction of main content (Exchange specific)
            main_content = None
            
            if "mexc.com" in url:
                main_content = soup.find('div', id='content') or \
                               soup.find('div', class_=re.compile(r'articleContent|article-content|article_content|article-detail|article-body|post-content|article_articleContent|article_articleDetailContent')) or \
                               soup.find('div', class_='main-container')
            elif "gate." in url:
                main_content = soup.find('div', id='article-detail-container') or \
                               soup.find('div', class_='article-content') or \
                               soup.find('div', class_='content')
            elif "binance.com" in url:
                main_content = soup.find('div', id='article-detail-container') or \
                               soup.find('div', class_=re.compile(r'rich-text-content|article-content|css-16l8z6d|announcement|post-body|detail-content')) or \
                               soup.find('article') or \
                               soup.find('main') or \
                               soup.find('div', class_='content')
            
            if main_content:
                soup = main_content
            else:
                # Heuristic: Find H1 and take its container if it's large enough
                h1 = soup.find('h1')
                if h1:
                    parent = h1.parent
                    if len(parent.get_text()) < 200 and parent.parent:
                        parent = parent.parent
                    soup = parent
            
            text = soup.get_text(" ", strip=True) 
            return self.extract_pairs_from_text(text)
            
        except Exception as e:
            logger.error(f"Failed to parse article {url}: {e}")
            return set()

    def extract_pairs_from_text(self, text: str) -> Set[str]:
        """
        Ищет пары в сыром тексте.
        """
        raw_matches = self.PAIR_PATTERN.finditer(text)
        
        result = set()
        for match in raw_matches:
            base = match.group(1)
            quote = match.group(2)
            
            base_upper = base.upper()
            
            # Пропускаем, если base - это ключевое слово
            if any(k.upper() == base_upper for k in self.IGNORE_KEYWORDS) or \
               base_upper in ("TRADING", "DELISTING", "PAIR", "LIST", "SUPPORT", "ZONE", "STATUS", "TOKEN", "COIN", "SPOT"):
                continue
            
            # Постобработка: если quote не была захвачена отдельно, 
            # проверяем не застряла ли она в конце base (например ICEUSDT)
            if not quote:
                for q in self.QUOTE_CURRENCIES:
                    if base_upper.endswith(q) and len(base_upper) > len(q):
                        base_upper = base_upper[:-len(q)]
                        break
            result.add(base_upper)
            
        return result
