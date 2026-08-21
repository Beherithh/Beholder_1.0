"""
Утилита для нормализации символов торговых пар к единому формату 'BASE/QUOTE'.

Используется в FileWatcherService и ApiRiskCheckerService для приведения
различных форматов бирж (BTC_USDT, BTCUSDT, BTC-USDT) к стандартному виду.
"""

import re
from typing import Optional

# Стандартные котировочные валюты, упорядоченные по частоте использования.
# Это суперсет всех котировок, встречающихся в API бирж и файлах-списках.
STANDARD_QUOTES = ("USDT", "BTC", "ETH", "USDC", "BNB", "SOL", "BUSD", "FDUSD", "TUSD", "USD")


def normalize_symbol(raw_symbol: str, fallback_quote: Optional[str] = None) -> str:
    """Нормализует символ торговой пары к формату 'BASE/QUOTE'.

    Поддерживает форматы:
        - 'BTC_USDT', 'BTC-USDT', 'BTC.USDT' -> 'BTC/USDT'
        - 'BTCUSDT'                            -> 'BTC/USDT'
        - 'BTC/USDT'                           -> 'BTC/USDT' (без изменений)
        - 'DOGE' + fallback_quote='USDT'       -> 'DOGE/USDT'

    Args:
        raw_symbol: Исходный символ пары.
        fallback_quote: Котировка по умолчанию (из имени файла и т.д.),
                        если её невозможно определить из самого символа.

    Returns:
        Нормализованный символ 'BASE/QUOTE' в верхнем регистре.
    """
    symbol = raw_symbol.strip().upper()

    # 1. Если есть разделитель (_  -  .) — заменяем на '/'
    if any(sep in symbol for sep in ("_", "-", ".")):
        return re.sub(r'[_\-.]', '/', symbol)

    # 2. Уже нормализован — возвращаем как есть
    if "/" in symbol:
        return symbol

    # 3. Склеенный формат (BTCUSDT) — ищем котировку в конце строки
    #    fallback_quote проверяется первым (приоритет контекста)
    quotes_to_check: list[str] = []
    if fallback_quote:
        quotes_to_check.append(fallback_quote.upper())
    quotes_to_check.extend(q for q in STANDARD_QUOTES if q not in quotes_to_check)

    for q in quotes_to_check:
        if symbol.endswith(q) and len(symbol) > len(q):
            base = symbol[:-len(q)]
            return f"{base}/{q}"

    # 4. Котировка не найдена внутри символа — используем fallback напрямую
    if fallback_quote:
        return f"{symbol}/{fallback_quote.upper()}"

    # 5. Невозможно определить котировку — возвращаем как есть
    return symbol


def normalize_symbol_for_exchange(symbol: str, exchange: str, market_type: str) -> str:
    """Нормализует символ для конкретной биржи и типа рынка.
    
    Некоторые биржи требуют специфичный формат символов в зависимости от типа рынка:
    - Bybit Linear Perpetual: 'BTC/USDT:USDT' (с settlement currency после двоеточия)
    - Bybit Inverse Perpetual: 'BTC/USD:BTC'
    - Spot markets: стандартный формат 'BTC/USDT'
    
    Args:
        symbol: Нормализованный символ в формате 'BASE/QUOTE'
        exchange: Название биржи (например, 'bybit', 'binance')
        market_type: Тип рынка ('spot', 'linear', 'inverse')
    
    Returns:
        Символ в формате, требуемом биржей для данного типа рынка.
    """
    exchange = exchange.upper()
    market_type = market_type.lower()
    
    # Bybit требует специальный формат для derivatives
    if exchange == 'BYBIT':
        if market_type == 'linear':
            # Linear perpetual требует формат 'BASE/QUOTE:SETTLEMENT'
            # Для USDT-margined это 'BTC/USDT:USDT'
            if ':' not in symbol and symbol.endswith('/USDT'):
                return f"{symbol}:USDT"
            elif ':' not in symbol and symbol.endswith('/USDC'):
                return f"{symbol}:USDC"
        elif market_type == 'inverse':
            # Inverse perpetual требует формат 'BASE/USD:BASE'
            # Например, 'BTC/USD:BTC'
            if ':' not in symbol and '/USD' in symbol:
                base = symbol.split('/')[0]
                return f"{symbol}:{base}"
    
    # Для остальных бирж или spot рынков возвращаем без изменений
    return symbol
