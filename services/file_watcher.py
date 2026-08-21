import asyncio
import json
import re
from pathlib import Path
from typing import List, Set, Tuple, Dict, Any
from loguru import logger
from sqlmodel import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MonitoredPair, MonitoringStatus, RiskLevel, Signal, MarketType
from utils.symbol_normalizer import normalize_symbol
from services.config import ConfigService

class FileWatcherService:
    """
    Сервис для чтения файлов с торговыми парами и синхронизации их с Базой Данных.
    Реализует логику Soft Delete (мягкого удаления).
    """

    def __init__(self, session_factory, config_service: ConfigService):
        """
        :param session_factory: Функция или генератор, возвращающая асинхронную сессию БД.
        :param config_service: Сервис конфигурации для получения списка файлов.
        """
        self.session_factory = session_factory
        self.config_service = config_service

    # Маппинг названий из файлов в ID для CCXT
    EXCHANGE_MAPPING = {
        "GATE": "GATEIO",
        # Сюда можно добавлять другие биржи по мере необходимости
    }
    
    # Маппинг ключевых слов типа рынка в enum MarketType
    MARKET_TYPE_KEYWORDS = {
        'futures': MarketType.LINEAR,
        'future': MarketType.LINEAR,
        'swap': MarketType.LINEAR,
        'linear': MarketType.LINEAR,
        'inverse': MarketType.INVERSE,
        'spot': MarketType.SPOT,
    }

    async def _read_files(self, file_items: List[Dict[str, str]]) -> Tuple[Set[Tuple[str, str, str, str]], List[str]]:
        """
        Приватный метод. Читает все файлы JSON и собирает уникальные пары.
        
        :param file_items: Список словарей [{'path': '...', 'name': '...'}, ...]
        :return: (Множество пар, Список отсутствующих файлов)
        """
        found_pairs = set()
        missing_files = []

        for item in file_items:
            path_str = item.get("path")
            label = item.get("name", "Unknown")
            
            if not path_str: continue

            path = Path(path_str)
            if not path.exists():
                logger.warning(f"Файл не найден: {path_str}")
                missing_files.append(path_str)
                continue

            # 1. Парсинг имени файла
            filename = path.name

            # Регулярка для извлечения биржи, типа рынка и валюты котирования.
            # Поддерживает форматы:
            # - Gate_instruments_USDT                    → spot (по умолчанию)
            # - Bybit Futures_instruments_USDT           → linear
            # - Bybit Swap_instruments_USDT              → linear
            # - 2_Bybit Linear_instruments_USDT          → linear
            # - Bybit Inverse_instruments_BTC            → inverse
            # - Kucoin spot_instruments_BTC              → spot
            #
            # Группы regex:
            # 1: Биржа (первое слово до пробела или `_`)
            # 2: Тип рынка (Futures, Swap, Linear, Inverse, Spot) - опционально
            # 3: Валюта котирования (последний сегмент перед расширением)
            match = re.match(
                r'^(?:\d+_)?([^ _]+)\s*(Futures?|Swap|Linear|Inverse|futures?|swap|linear|inverse|spot|Spot)?.*?_[^_ ]+_([^_ ]+?)(?:\.[^.]+)?$',
                filename
            )
            
            if match:
                raw_exchange = match.group(1).upper()
                market_type_word = match.group(2)  # 'Futures', 'Swap', None, и т.д.
                quote_currency = match.group(3).upper()
                
                # Нормализация имени биржи (например GATE -> GATEIO)
                exchange_name = self.EXCHANGE_MAPPING.get(raw_exchange, raw_exchange)
                
                # Определяем тип рынка
                if market_type_word:
                    market_type = self.MARKET_TYPE_KEYWORDS.get(
                        market_type_word.lower(),
                        MarketType.SPOT
                    )
                else:
                    market_type = MarketType.SPOT  # По умолчанию
            else:
                logger.warning(f"Не удалось извлечь данные из имени: {filename}. Используем defaults.")
                exchange_name = "UNKNOWN"
                quote_currency = None
                market_type = MarketType.SPOT

            # 2. Парсинг содержимого
            try:
                content = path.read_text(encoding='utf-8')
                data = json.loads(content)
                
                # Ищем список пар в ключе "listHelper"
                items = data.get("listHelper", [])
                
                if not items:
                    logger.warning(f"В файле {filename} нет ключа 'listHelper'.")
                    continue

                for pair_item in items:
                    raw_symbol = pair_item.get("symbol", "")
                    if raw_symbol:
                        # Нормализация: BTC_USDT -> BTC/USDT, BTCUSDT -> BTC/USDT
                        # quote_currency из имени файла используется как fallback
                        normalized_symbol = normalize_symbol(raw_symbol, fallback_quote=quote_currency)
                        
                        # Если котировку не удалось определить — символ не содержит '/'
                        if '/' not in normalized_symbol and not quote_currency:
                            logger.warning(f"Символ '{raw_symbol}' пропущен: не удалось определить котировку.")
                            continue

                        # Добавляем market_type в кортеж found_pairs
                        found_pairs.add((exchange_name, normalized_symbol, market_type, path_str, label))
                    
            except json.JSONDecodeError:
                logger.error(f"Ошибка парсинга JSON в {path_str}")
            except Exception as e:
                logger.error(f"Ошибка чтения {path_str}: {e}")

        logger.info(f"Всего найдено пар в файлах: {len(found_pairs)}")
        return found_pairs, missing_files

    async def sync_files(self, file_dict_list: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Основной метод синхронизации.
        :param file_dict_list: [{'path': 'C:/...', 'name': 'Gate 1'}, ...]
        """
        stats = {"added": 0, "reactivated": 0, "archived": 0, "unchanged": 0, "missing_files": []}
        
        # 1. Читаем файлы
        file_pairs_set, missing_files = await self._read_files(file_dict_list)
        stats["missing_files"] = missing_files
        
        # Агрегация: (exchange, symbol, market_type) -> {labels: set(), files: set()}
        aggregated_map = {}
        
        for (ex, sym, mtype, src, lbl) in file_pairs_set:
            key = (ex, sym, mtype)  # Добавляем market_type в ключ
            if key not in aggregated_map:
                aggregated_map[key] = {"labels": set(), "files": set()}
            
            aggregated_map[key]["labels"].add(lbl)
            aggregated_map[key]["files"].add(src)

        async with self.session_factory() as session:
            statement = select(MonitoredPair)
            result = await session.execute(statement)
            db_pairs = result.scalars().all()
            
            db_pairs_map = { (p.exchange, p.symbol, p.market_type): p for p in db_pairs }

            # 3. Обработка ВХОДЯЩИХ
            for (exchange, symbol, market_type), data in aggregated_map.items():
                # Формируем JSON строку меток (сортируем для стабильности)
                labels_list = sorted(list(data["labels"]))
                labels_json = json.dumps(labels_list, ensure_ascii=False)
                primary_file = list(data["files"])[0]

                if (exchange, symbol, market_type) in db_pairs_map:
                    existing_pair = db_pairs_map[(exchange, symbol, market_type)]

                    if existing_pair.monitoring_status == MonitoringStatus.INACTIVE:
                        existing_pair.monitoring_status = MonitoringStatus.ACTIVE
                        existing_pair.risk_level = RiskLevel.NORMAL
                        stats["reactivated"] += 1
                    else:
                        stats["unchanged"] += 1

                    if existing_pair.source_label != labels_json:
                        existing_pair.source_label = labels_json
                    
                    if existing_pair.source_file != primary_file:
                        existing_pair.source_file = primary_file
                        
                else:
                    new_pair = MonitoredPair(
                        exchange=exchange,
                        symbol=symbol,
                        market_type=market_type,  # NEW: Сохраняем тип рынка
                        source_file=primary_file,
                        source_label=labels_json,
                        monitoring_status=MonitoringStatus.ACTIVE,
                        risk_level=RiskLevel.NORMAL
                    )
                    session.add(new_pair)
                    stats["added"] += 1

            # 4. Обработка ИСЧЕЗНУВШИХ
            for (exchange, symbol, market_type), db_pair in db_pairs_map.items():
                if (exchange, symbol, market_type) not in aggregated_map:
                    if db_pair.monitoring_status == MonitoringStatus.ACTIVE:
                        # Была активна, но исчезла из файлов -> УДАЛЯЕМ (Мягко)
                        db_pair.monitoring_status = MonitoringStatus.INACTIVE
                        db_pair.risk_level = RiskLevel.NORMAL
                        await session.execute(delete(Signal).where(Signal.pair_id == db_pair.id))
                        stats["archived"] += 1
            
            # Сохраняем изменения
            await session.commit()
            
        logger.success(f"Синхронизация завершена: {stats}")
        return stats

    async def sync_from_settings(self) -> Dict[str, Any]:
        """
        Загружает список файлов из ConfigService и выполняет синхронизацию.
        Возвращает словарь со статистикой.
        """
        files_list = await self.config_service.get_watched_files()
        
        if not files_list:
            logger.warning("Нет файлов для синхронизации в настройках")
            return {"error": "Нет настроенных файлов"}
        
        # Синхронизация
        stats = await self.sync_files(files_list)
        return stats
