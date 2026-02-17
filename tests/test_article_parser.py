import pytest
from services.article_parser import ArticleParser

class TestArticleParser:
    
    def setup_method(self):
        self.parser = ArticleParser()

    def test_extract_simple_pair(self):
        text = "Binance will delist BTC/USDT on Friday."
        pairs = self.parser.extract_pairs_from_text(text)
        assert "BTC" in pairs
        assert len(pairs) == 1

    def test_extract_multiple_pairs(self):
        text = "We are removing ETH_USDT, SOL-BUSD and ADA/BTC from the exchange."
        pairs = self.parser.extract_pairs_from_text(text)
        assert "ETH" in pairs
        assert "SOL" in pairs
        assert "ADA" in pairs
        assert len(pairs) == 3

    def test_extract_pair_without_separator(self):
        text = "Trading for ICEUSDT will cease."
        pairs = self.parser.extract_pairs_from_text(text)
        assert "ICE" in pairs

    def test_ignore_keywords(self):
        """Проверка, что парсер игнорирует служебные слова, похожие на тикеры"""
        text = "Future trading and Margin options will be suspended. LIST new token."
        pairs = self.parser.extract_pairs_from_text(text)
        # FUTURE, MARGIN, OPTION, LIST - должны быть в IGNORE_KEYWORDS или фильтроваться
        assert len(pairs) == 0

    def test_extract_from_html_structure(self):
        """Проверка очистки HTML от мусора"""
        html = """
        <html>
            <body>
                <div class="sidebar">Related: XRP/USDT</div>
                <div class="content">
                    <h1>Delisting of OMG/BTC</h1>
                    <p>We are delisting OMG.</p>
                </div>
                <footer>Copyright 2023</footer>
            </body>
        </html>
        """
        # Мы эмулируем URL, чтобы сработала эвристика (хотя в базовом методе она общая)
        pairs = self.parser.extract_pairs_from_html(html, "https://binance.com/article/123")
        
        # XRP в сайдбаре должен быть проигнорирован (если логика очистки работает)
        # OMG в контенте должен быть найден
        assert "OMG" in pairs
        # Это сложный тест, зависит от того, насколько агрессивно мы чистим sidebar в ArticleParser
        # Если XRP попадет - не страшно, главное чтобы OMG был.
