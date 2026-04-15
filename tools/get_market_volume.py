import asyncio
import ccxt.async_support as ccxt
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from enum import Enum

# Configuration - change this symbol to work with different trading pairs
DEFAULT_SYMBOL = 'ULTIMA/USDT'


class MarketType(Enum):
    SPOT = 'spot'
    SWAP = 'swap'


class MarketDataFetcher:
    """Class for fetching market data from different market types."""

    def __init__(self):
        self.exchange = ccxt.mexc()

    async def get_volume_30d(self, symbol: str = DEFAULT_SYMBOL, market_type: MarketType = MarketType.SPOT) -> float:
        """
        Gets total trading volume for specified market type over last 30 days.
        
        Args:
            symbol: Trading pair symbol (e.g., 'QUBIC/USDT')
            market_type: Market type - SPOT or SWAP
        
        Returns:
            float: Average hourly volume in USDT.
        """
        try:
            # Calculate timestamp for 30 days ago (in milliseconds)
            since = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000)

            # For swap markets, we might need different symbol format
            original_symbol = symbol
            if market_type == MarketType.SWAP:
                # Some exchanges use different symbol format for futures
                symbol = self._format_swap_symbol(symbol)

            # Load daily candles (1d)
            ohlcv: List[List[float]] = await self.exchange.fetch_ohlcv(
                symbol,
                timeframe='1d',
                since=since,
                params={'type': market_type.value} if market_type == MarketType.SWAP else {}
            )

            # Get contract size for swap markets
            contract_size = 1.0
            if market_type == MarketType.SWAP:
                market_info = await self.get_market_info(original_symbol, market_type)
                contract_size = market_info.get('contract_size', 1.0)

            total_usdt_volume = 0.0

            for candle in ohlcv:
                # candle[4] - Close price
                # candle[5] - Volume (base currency for spot, contracts for swap)
                close_price = candle[4]
                volume = candle[5]

                # For swap markets, convert contracts to actual coin volume
                if market_type == MarketType.SWAP:
                    base_volume = volume * contract_size
                else:
                    base_volume = volume

                # Calculate approximate volume in USDT for this candle
                total_usdt_volume += base_volume * close_price

            return total_usdt_volume / 30 / 24

        except Exception as e:
            print(f"Error fetching {market_type.value} data for {symbol}: {e}")
            return 0.0
        finally:
            # Always close the exchange connection
            await self.exchange.close()

    async def get_market_info(self, symbol: str = DEFAULT_SYMBOL, market_type: MarketType = MarketType.SPOT) -> Dict[
        str, Any]:
        """
        Gets market information including contract size for futures.
        
        Args:
            symbol: Trading pair symbol (e.g., 'QUBIC/USDT')
            market_type: Market type - SPOT or SWAP
        
        Returns:
            Dict with market information including contract size
        """
        try:
            # For swap markets, format symbol appropriately
            if market_type == MarketType.SWAP:
                symbol = self._format_swap_symbol(symbol)

            # Load markets to get contract information
            await self.exchange.load_markets()

            # Get market info
            market = self.exchange.markets.get(symbol)
            if not market:
                return {}

            return {
                'contract_size': market.get('contractSize', 1.0),
                'active': market.get('active', False),
                'type': market.get('type', 'spot'),
                'spot': market.get('spot', False),
                'swap': market.get('swap', False),
                'future': market.get('future', False),
            }

        except Exception as e:
            print(f"Error getting market info for {market_type.value} {symbol}: {e}")
            return {}

    async def get_last_candles(self, symbol: str = DEFAULT_SYMBOL, market_type: MarketType = MarketType.SPOT,
                               limit: int = 5) -> List[List[float]]:
        """
        Gets the last N candles from specified market.
        
        Args:
            symbol: Trading pair symbol (e.g., 'QUBIC/USDT')
            market_type: Market type - SPOT or SWAP
            limit: Number of candles to get (default: 5)
        
        Returns:
            List of OHLCV candles: [[timestamp, open, high, low, close, volume], ...]
        """
        try:
            # For swap markets, format symbol appropriately
            if market_type == MarketType.SWAP:
                symbol = self._format_swap_symbol(symbol)

            # Get last N candles
            ohlcv = await self.exchange.fetch_ohlcv(
                symbol,
                timeframe='1d',
                limit=limit,
                params={'type': market_type.value} if market_type == MarketType.SWAP else {}
            )

            return ohlcv

        except Exception as e:
            print(f"Error fetching candles for {market_type.value} {symbol}: {e}")
            return []

    def _format_swap_symbol(self, symbol: str) -> str:
        """
        Formats symbol for swap/futures markets.
        Different exchanges may use different formats.
        """
        # Example: QUBIC/USDT -> QUBICUSDT or QUBIC-PERP
        base, quote = symbol.split('/')
        return f"{base}/{quote}:USDT"  # Common format for perpetual swaps


async def fetch_multiple_markets():
    """Demonstrates fetching data from both spot and swap markets."""
    fetcher = MarketDataFetcher()

    print(f"=== Market Data for {DEFAULT_SYMBOL} ===\n")

    # Fetch from spot market
    print("--- SPOT Market ---")
    spot_volume = await fetcher.get_volume_30d(DEFAULT_SYMBOL, MarketType.SPOT)
    print(f"Average hourly volume: {spot_volume:,.2f} USDT")

    spot_candles = await fetcher.get_last_candles(DEFAULT_SYMBOL, MarketType.SPOT, 5)
    print(f"Last 5 candles:")
    for i, candle in enumerate(reversed(spot_candles), 1):
        timestamp = datetime.fromtimestamp(candle[0] / 1000).strftime('%Y-%m-%d')
        close_price = candle[4]
        base_volume = candle[5]
        usdt_volume = base_volume * close_price
        print(
            f"  {i}. {timestamp}: O={candle[1]:.10f} H={candle[2]:.10f} L={candle[3]:.10f} C={candle[4]:.10f} V={base_volume:.0f} (${usdt_volume:,.2f})")

    print()

    # Fetch from swap market  
    print("--- SWAP Market ---")
    swap_volume = await fetcher.get_volume_30d(DEFAULT_SYMBOL, MarketType.SWAP)
    print(f"Average hourly volume: {swap_volume:,.2f} USDT")

    # Get market info for contract size
    swap_market_info = await fetcher.get_market_info(DEFAULT_SYMBOL, MarketType.SWAP)
    contract_size = swap_market_info.get('contract_size', 1.0)
    print(f"Contract size: {contract_size}")

    swap_candles = await fetcher.get_last_candles(DEFAULT_SYMBOL, MarketType.SWAP, 5)
    print(f"Last 5 candles:")
    for i, candle in enumerate(reversed(swap_candles), 1):
        timestamp = datetime.fromtimestamp(candle[0] / 1000).strftime('%Y-%m-%d')
        close_price = candle[4]
        contract_volume = candle[5]  # Volume in contracts
        # Calculate actual coin volume: contracts * contract_size
        base_volume = contract_volume * contract_size
        usdt_volume = base_volume * close_price
        print(
            f"  {i}. {timestamp}: O={candle[1]:.10f} H={candle[2]:.10f} L={candle[3]:.10f} C={candle[4]:.10f} V={base_volume:.0f} (${usdt_volume:,.2f})")

    await fetcher.exchange.close()


if __name__ == "__main__":
    asyncio.run(fetch_multiple_markets())
