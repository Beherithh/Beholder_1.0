import httpx
from loguru import logger
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    MonitoredPair, DelistingEvent, DelistingEventType, RiskLevel
)

class ApiRiskCheckerService:
    """
    Сервис для проверки API бирж на наличие ST/Risk статусов.
    """

    API_SOURCES = [
        {
            "name": "GATEIO",
            "url": "https://api.gateio.ws/api/v4/spot/currency_pairs",
            "symbol_key": "id",           # Field for symbol (e.g., "BTC_USDT")
            "st_key": "st_tag",           # Field for ST status (True/False or string)
        },
        {
            "name": "MEXC",
            "url": "https://api.mexc.com/api/v3/exchangeInfo",
            "symbol_key": "symbol",       # Field for symbol "BTCUSDT"
            "st_key": "st",               # TRUE = ST tag assigned (risk)
        },
        {
            "name": "KUCOIN",
            "url": "https://api.kucoin.com/api/v2/symbols",
            "symbol_key": "symbol",       # Field for symbol (e.g., "BTC-USDT")
            "st_key": "st",               # Field for ST status
        },
    ]

    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def check_api_risks(self) -> bool:
        """
        Проверка API бирж на статус ST/Risk.
        Возвращает True, если были изменения (созданы сигналы восстановления или новые события),
        что требует запуска матчинга.
        """
        logger.info("Проверка API на ST/Risk статусы (Direct & Cross)...")
        
        changes_detected = False

        async with self.session_factory() as session:
            # 1. Загружаем активные пары
            db_pairs = (await session.execute(
                select(MonitoredPair).where(MonitoredPair.monitoring_status == "active")
            )).scalars().all()
            
            if not db_pairs:
                return False

            signals_created = 0 # Используем как флаг изменений (не только сигналы, но и recovery)
            
            # 2. Собираем данные со всех API в единую структуру
            all_api_data = {}
            quote_currencies = {"USDT", "BTC", "ETH", "BUSD", "BNB", "SOL", "USDC"}

            async with httpx.AsyncClient(timeout=30.0) as client:
                for source in self.API_SOURCES:
                    ex_name = source["name"]
                    logger.info(f"[{ex_name}] Fetching API: {source['url']}")
                    all_api_data[ex_name] = {}
                    
                    try:
                        resp = await client.get(source["url"])
                        if resp.status_code != 200:
                            logger.warning(f"[{ex_name}] API returned {resp.status_code}")
                            continue
                            
                        data = resp.json()
                        
                        # Normalize format
                        if isinstance(data, dict):
                            if "symbols" in data:
                                items = data["symbols"]
                            elif "data" in data:
                                items = data["data"]
                            else:
                                logger.warning(f"[{ex_name}] Unexpected API response format (no symbols/data key)")
                                continue
                        elif isinstance(data, list):
                            items = data
                        else:
                            logger.warning(f"[{ex_name}] Unexpected API response format (not dict/list)")
                            continue
                        
                        # Build Normalize Map
                        for item in items:
                            symbol_key = source.get("symbol_key", "symbol")
                            raw_symbol = item.get(symbol_key, "")
                            if not raw_symbol:
                                continue
                            
                            # Нормализуем к формату БД: BTC/USDT
                            if "_" in raw_symbol:
                                normalized = raw_symbol.replace("_", "/")
                            elif "-" in raw_symbol:
                                normalized = raw_symbol.replace("-", "/")
                            elif "/" in raw_symbol:
                                normalized = raw_symbol
                            else:
                                normalized = raw_symbol
                                for quote in quote_currencies:
                                    if raw_symbol.endswith(quote) and len(raw_symbol) > len(quote):
                                        base = raw_symbol[:-len(quote)]
                                        normalized = f"{base}/{quote}"
                                        break
                            
                            # Группируем данные по базовой валюте
                            base_currency = normalized.split('/')[0].upper()
                            if base_currency not in all_api_data[ex_name]:
                                all_api_data[ex_name][base_currency] = []
                            
                            all_api_data[ex_name][base_currency].append({
                                "symbol": normalized.upper(),
                                "item": item
                            })
                            
                    except Exception as api_err:
                        logger.error(f"[{ex_name}] API Fetch Error: {api_err}")

            # 3. Анализируем данные API
            for pair in db_pairs:
                current_ex = pair.exchange.upper()
                pair_symbol = pair.symbol.upper()
                base_currency = pair_symbol.split('/')[0]
                
                # --- A. Populating DelistingEvent from API (ST status) ---
                for ex_name, ex_data in all_api_data.items():
                    ticker_pairs = ex_data.get(base_currency, [])
                    
                    st_triggering_pairs = []
                    source_cfg = next((s for s in self.API_SOURCES if s["name"] == ex_name), {})
                    st_key = source_cfg.get("st_key", "st")
                    
                    for entry in ticker_pairs:
                        api_item = entry["item"]
                        st_value = api_item.get(st_key)
                        is_risk = (st_value == True or str(st_value).lower() == "true" or st_value == 1 or st_value == "1")
                        
                        if is_risk:
                            st_triggering_pairs.append(entry["symbol"])
                    
                    if st_triggering_pairs:
                        trigger_info = ", ".join(st_triggering_pairs)
                        stmt = select(DelistingEvent).where(
                            DelistingEvent.exchange == ex_name,
                            DelistingEvent.symbol == base_currency,
                            DelistingEvent.announcement_url == source_cfg.get("url", "API"),
                            DelistingEvent.type == DelistingEventType.ST
                        )
                        existing_event = (await session.execute(stmt)).scalars().first()
                        
                        event_title = f"API ST tag: {trigger_info}"
                        
                        if not existing_event:
                            new_event = DelistingEvent(
                                exchange=ex_name,
                                symbol=base_currency,
                                announcement_title=event_title,
                                announcement_url=source_cfg.get("url", "API"),
                                type=DelistingEventType.ST
                            )
                            session.add(new_event)
                            changes_detected = True
                            logger.info(f"[{ex_name}] New ST status detected for ticker {base_currency} (via {trigger_info})")
                        else:
                            if existing_event.announcement_title != event_title:
                                existing_event.announcement_title = event_title
                                session.add(existing_event)
                                changes_detected = True

                # --- B. Recovery Logic (Unique to API) ---
                native_ticker_pairs = all_api_data.get(current_ex, {}).get(base_currency, [])
                if native_ticker_pairs and pair.risk_level == RiskLevel.RISK_ZONE:
                    source_cfg = next((s for s in self.API_SOURCES if s["name"] == current_ex), {})
                    st_key = source_cfg.get("st_key", "st")
                    
                    any_st_active = False
                    for entry in native_ticker_pairs:
                        st_value = entry["item"].get(st_key)
                        if (st_value == True or str(st_value).lower() == "true" or st_value == 1 or st_value == "1"):
                            any_st_active = True
                            break
                    
                    if not any_st_active:
                        pair.risk_level = RiskLevel.NORMAL
                        session.add(pair)
                        signals_created += 1
                        changes_detected = True
                        logger.info(f"[{current_ex}] {pair.symbol} - ST Cleared for ticker {base_currency} (Recovery).")

            if changes_detected or signals_created > 0:
                await session.commit()
                
        return changes_detected
