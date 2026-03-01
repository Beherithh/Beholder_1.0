"""
Утилита для нормализации символов торговых пар к единому формату 'BASE/QUOTE'.

Используется в FileWatcherService и ApiRiskCheckerService для приведения
различных форматов бирж (BTC_USDT, BTCUSDT, BTC-USDT) к стандартному виду.
"""

import re
from typing import Optional

# Стандартные котировочные валюты, упорядоченные по частоте использования.
# Это суперсет всех котировок, встречающихся в API бирж и файлах-списках.
STANDARD_QUOTES = ("USDT", "BTC", "ETH", "USDC", "BNB", "SOL", "BUSD", "FDUSD", "TUSD")


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
