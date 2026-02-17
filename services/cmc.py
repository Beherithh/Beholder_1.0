from typing import List, Dict, Any, Optional
import asyncio
from datetime import datetime, timedelta, timezone
import json
import httpx
from loguru import logger
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MonitoredPair, MonitoringStatus, Signal, SignalType

class CMCService:
    """
    Сервис для работы с API CoinMarketCap.
    Основная задача: получение ранга криптовалют (cmc_rank).
    """
    BASE_URL = "https://pro-api.coinmarketcap.com"
    
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def sync_ranks(self) -> str:
        logger.info(f"=== Запуск обновления рангов СМС ===")

        # Получаем конфиг через ConfigService
        from services.system import get_config_service
        config = await get_config_service().get_cmc_config()

        if not config.api_key:
            logger.warning("CMC API Key не найден. Пропуск обновления рангов.")
            return "No API Key"

        """
        Обновляет ранги для всех АКТИВНЫХ пар.
        Стратегия:
        1. Получить уникальные базовые валюты (BTC, ETH...)
        2. Разбить на чанки по 100 штук
        3. Запросить quotes/latest
        4. Обновить поле cmc_rank в БД
        """
        async with self.session_factory() as session:
            # 1. Получаем уникальные базовые валюты из активных пар
            stmt = select(MonitoredPair).where(MonitoredPair.monitoring_status == MonitoringStatus.ACTIVE)
            pairs = (await session.execute(stmt)).scalars().all()
            
            if not pairs:
                return "Нет активных пар"

            # Карта: BaseCurrency -> List[MonitoredPair]
            currency_map: Dict[str, List[MonitoredPair]] = {}
            
            for pair in pairs:
                symbol = pair.symbol.upper()
                base = symbol
                if '/' in symbol:
                    base = symbol.split('/')[0]
                elif '_' in symbol:
                    base = symbol.split('_')[0]
                
                if base not in currency_map:
                    currency_map[base] = []
                currency_map[base].append(pair)
            
            unique_currencies = list(currency_map.keys())
            logger.info(f"Найдено {len(unique_currencies)} уникальных валют для обновления ранга.")

            # 2. Разбиваем на чанки по 100
            chunk_size = 100
            updated_count = 0
            
            headers = {
                'X-CMC_PRO_API_KEY': config.api_key,
                'Accept': 'application/json'
            }

            async with httpx.AsyncClient() as client:
                for i in range(0, len(unique_currencies), chunk_size):
                    chunk = unique_currencies[i : i + chunk_size]
                    symbols_str = ",".join(chunk)
                    
                    try:
                        url = f"{self.BASE_URL}/v1/cryptocurrency/quotes/latest"
                        logger.debug(f"Запрос CMC для {len(chunk)} монет...")
                        
                        response = await client.get(url, headers=headers, params={"symbol": symbols_str})
                        
                        if response.status_code == 200:
                            data = response.json().get("data", {})
                            
                            # 3. Обрабатываем ответ
                            for symbol, info in data.items():
                                coin_obj = None
                                if isinstance(info, list):
                                    valid_coins = [c for c in info if c.get('cmc_rank') is not None]
                                    if valid_coins:
                                        coin_obj = min(valid_coins, key=lambda x: x['cmc_rank'])
                                else:
                                    coin_obj = info
                                
                                if coin_obj and coin_obj.get('cmc_rank'):
                                    rank = int(coin_obj['cmc_rank'])
                                    
                                    # Обновляем все пары с этим тикером
                                    if symbol in currency_map:
                                        for pair in currency_map[symbol]:
                                            if pair.cmc_rank != rank:
                                                pair.cmc_rank = rank
                                                updated_count += 1
                                            
                                            # Проверка алерта с использованием порога из конфига
                                            if rank > config.rank_threshold:
                                                await self._process_alert(session, pair, rank)
                        else:
                            logger.error(f"Ошибка CMC API {response.status_code}: {response.text}")
                            
                        # Пауза между запросами
                        await asyncio.sleep(1) 
                        
                    except Exception as e:
                        logger.error(f"Ошибка при запросе к CMC: {e}")

            await session.commit()
            msg = f"Обновлено рангов для {updated_count} пар (всего валют: {len(unique_currencies)})"
            logger.info(msg)
            return msg

    async def _process_alert(self, session: AsyncSession, pair: MonitoredPair, rank: int):
        """
        Проверяет и отправляет алерт о низком ранге.
        """
        # Поиск недавнего сигнала (за последние 3 дня хотя бы)
        cutoff = datetime.now(timezone.utc) - timedelta(days=3)
        stmt = select(Signal).where(
            Signal.type == SignalType.RANK_WARNING,
            Signal.pair_id == pair.id,
            Signal.created_at >= cutoff
        )
        existing = (await session.execute(stmt)).first()
        if existing:
            return

        # Распаковка названий списков из JSON
        lists_str = pair.source_label or "Unknown"
        try:
            loaded = json.loads(lists_str)
            if isinstance(loaded, list):
                lists_str = ", ".join(loaded)
            elif isinstance(loaded, str):
                lists_str = loaded
        except:
            pass

        msg_text = f"⚠️ <b>Low Rank Warning</b>\n\n" \
                   f"Coin: <b>{pair.symbol}</b>\n" \
                   f"Current Rank: <b>#{rank}</b>\n" \
                   f"Lists: {lists_str}"
        
        # Создаем сигнал
        signal = Signal(
            type=SignalType.RANK_WARNING,
            pair_id=pair.id,
            raw_message=msg_text,
            is_sent=False,
            created_at=datetime.now(timezone.utc)
        )
        session.add(signal)
        await session.flush() # чтобы получить ID

        # Отправляем
        from services.system import get_telegram_service
        tg = get_telegram_service()
        if tg:
            try:
                sent = await tg.send_message(msg_text)
                if sent:
                    signal.is_sent = True
                    signal.sent_at = datetime.now(timezone.utc)
            except Exception as e:
                logger.error(f"Failed to send CMC alert: {e}")
