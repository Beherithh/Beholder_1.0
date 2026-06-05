import asyncio
import ccxt.async_support as ccxt
from datetime import datetime, timedelta, timezone
from typing import List

exchange = ccxt.gateio()
symbol = 'XEM/USDT'
DAYS = 15

async def fetch_xem_volume_30d() -> float:
    """
    Получает суммарный объем торгов на Gate.io за последние 30 дней.
    Returns:
        float: Суммарный объем в USDT.
    """
    # 1. Инициализируем биржу

    
    try:
        # 2. Вычисляем временную метку 30-дневной давности (в миллисекундах)
        since = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp() * 1000)
        
        # 3. Загружаем дневные свечи (1d)
        # OHLCV: [timestamp, open, high, low, close, volume]
        ohlcv: List[List[float]] = await exchange.fetch_ohlcv(symbol, timeframe='1d', since=since)
        
        total_usdt_volume = 0.0
        
        for candle in ohlcv:
            # candle[4] - Close price (цена закрытия)
            # candle[5] - Volume (объем в базовой валюте, т.е. в XEM)
            close_price = candle[4]
            base_volume = candle[5]
            
            # Рассчитываем примерный объем в USDT для этой свечи
            # Для более точного расчета на биржах иногда есть отдельное поле quote_volume,
            # но классический OHLCV требует умножения цены на объем.
            total_usdt_volume += base_volume * close_price
            
        return total_usdt_volume / DAYS / 24


    except Exception as e:
        print(f"Произошла ошибка при получении данных: {e}")
        return 0.0
    finally:
        # Обязательно закрываем соединение с биржей
        await exchange.close()

if __name__ == "__main__":
    total = asyncio.run(fetch_xem_volume_30d())
    print(f"Общий объем торгов в час {symbol} за {DAYS} дней: {total:,.2f} USDT")
