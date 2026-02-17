import asyncio
import json
import re
from pathlib import Path
from typing import List, Set, Tuple, Dict
from loguru import logger
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MonitoredPair, MonitoringStatus, RiskLevel

class FileWatcherService:
    """
    Сервис для чтения файлов с торговыми парами и синхронизации их с Базой Данных.
    Реализует логику Soft Delete (мягкого удаления).
    """

    def __init__(self, session_factory):
        """
        :param session_factory: Функция или генератор, возвращающая асинхронную сессию БД.
        """
        self.session_factory = session_factory

    # Маппинг названий из файлов в ID для CCXT
    EXCHANGE_MAPPING = {
        "GATE": "GATEIO",
        # Сюда можно добавлять другие биржи по мере необходимости
    }

    async def _read_files(self, file_items: List[Dict[str, str]]) -> Set[Tuple[str, str, str, str]]:
        """
        Приватный метод. Читает все файлы JSON и собирает уникальные пары.
        
        :param file_items: Список словарей [{'path': '...', 'name': '...'}, ...]
        :return: Множество кортежей: {(exchange, symbol, source_file_path, source_label), ...}
        """
        found_pairs = set()

        for item in file_items:
            path_str = item.get("path")
            label = item.get("name", "Unknown")
            
            if not path_str: continue

            path = Path(path_str)
            if not path.exists():
                logger.warning(f"Файл не найден: {path_str}")
                continue

            # 1. Парсинг имени файла
            filename = path.name

            # Регулярка для извлечения биржи и валюты котирования (например, USDT).
            # Поддерживает форматы:
            # - Gate_instruments_USDT
            # - 2_Gate_instruments_USDT
            # - Gate_ANYTHING_USDT.json
            match = re.search(r'(?:^\d+_)?([^_]+)_.+_([^_]+)$', filename)
            
            if match:
                raw_exchange = match.group(1).upper()
                # Нормализация имени биржи (например GATE -> gateio)
                exchange_name = self.EXCHANGE_MAPPING.get(raw_exchange, raw_exchange)
                quote_currency = match.group(2).upper()
            else:
                logger.warning(f"Не удалось извлечь данные из имени: {filename}. Defaults.")
                exchange_name = "UNKNOWN"
                quote_currency = ""

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
                        # Нормализация символа
                        normalized_symbol = raw_symbol.upper()
                        
                        # 1. Если есть разделитель "_" или "-" или "." -> меняем на "/"
                        if any(sep in normalized_symbol for sep in ("_", "-", ".")):
                            normalized_symbol = re.sub(r'[_\-\.]', '/', normalized_symbol)
                        
                        # 2. Если разделителя нет (AXSUSDT), ищем известную котируемую валюту (Quote)
                        elif "/" not in normalized_symbol:
                            # Сначала пробуем ту, что была в имени файла (приоритет)
                            quotes_to_check = [quote_currency] if quote_currency else []
                            # Затем все остальные стандартные
                            standard_quotes = ["USDT", "BTC", "ETH", "USDC", "BNB", "fdusd", "tusd", "busd"]
                            for q in standard_quotes:
                                q_up = q.upper()
                                if q_up not in quotes_to_check:
                                    quotes_to_check.append(q_up)
                            
                            for q in quotes_to_check:
                                if normalized_symbol.endswith(q) and len(normalized_symbol) > len(q):
                                    base = normalized_symbol[:-len(q)]
                                    normalized_symbol = f"{base}/{q}"
                                    break
                        
                        found_pairs.add((exchange_name, normalized_symbol, path_str, label))
                    
            except json.JSONDecodeError:
                logger.error(f"Ошибка парсинга JSON в {path_str}")
            except Exception as e:
                logger.error(f"Ошибка чтения {path_str}: {e}")

        logger.info(f"Всего найдено пар в файлах: {len(found_pairs)}")
        return found_pairs

    async def sync_files(self, file_dict_list: List[Dict[str, str]]) -> Dict[str, int]:
        """
        Основной метод синхронизации.
        :param file_dict_list: [{'path': 'C:/...', 'name': 'Gate 1'}, ...]
        """
        stats = {"added": 0, "reactivated": 0, "archived": 0, "unchanged": 0}
        
        # 1. Читаем файлы (передаем список словарей)
        file_pairs_set = await self._read_files(file_dict_list)
        
        # Агрегация: (exchange, symbol) -> {labels: set(), files: set()}
        aggregated_map = {}
        
        for (ex, sym, src, lbl) in file_pairs_set:
            key = (ex, sym)
            if key not in aggregated_map:
                aggregated_map[key] = {"labels": set(), "files": set()}
            
            aggregated_map[key]["labels"].add(lbl)
            aggregated_map[key]["files"].add(src)

        async with self.session_factory() as session:
            statement = select(MonitoredPair)
            result = await session.execute(statement)
            db_pairs = result.scalars().all()
            
            db_pairs_map = { (p.exchange, p.symbol): p for p in db_pairs }

            # 3. Обработка ВХОДЯЩИХ
            for (exchange, symbol), data in aggregated_map.items():
                # Формируем JSON строку меток (сортируем для стабильности)
                labels_list = sorted(list(data["labels"]))
                labels_json = json.dumps(labels_list, ensure_ascii=False)
                
                # Исходный файл берем последний (или первый), так как поле source_file строковое.
                primary_file = list(data["files"])[0] 

                if (exchange, symbol) in db_pairs_map:
                    # Обновление существующей
                    existing_pair = db_pairs_map[(exchange, symbol)]
                    
                    # Проверяем изменения
                    changes_needed = False
                    
                    if existing_pair.monitoring_status == MonitoringStatus.INACTIVE:
                        existing_pair.monitoring_status = MonitoringStatus.ACTIVE
                        stats["reactivated"] += 1
                        changes_needed = True
                    else:
                        stats["unchanged"] += 1

                    # Обновляем метаданные
                    if existing_pair.source_label != labels_json:
                        existing_pair.source_label = labels_json
                        changes_needed = True
                    
                    if existing_pair.source_file != primary_file:
                        existing_pair.source_file = primary_file
                        changes_needed = True 
                        
                else:
                    # Создание новой
                    new_pair = MonitoredPair(
                        exchange=exchange,
                        symbol=symbol,
                        source_file=primary_file,
                        source_label=labels_json,
                        monitoring_status=MonitoringStatus.ACTIVE,
                        risk_level=RiskLevel.NORMAL
                    )
                    session.add(new_pair)
                    stats["added"] += 1

            # 4. Обработка ИСЧЕЗНУВШИХ
            for (exchange, symbol), db_pair in db_pairs_map.items():
                if (exchange, symbol) not in aggregated_map:
                    if db_pair.monitoring_status == MonitoringStatus.ACTIVE:
                        # Была активна, но исчезла из файлов -> УДАЛЯЕМ (Мягко)
                        db_pair.monitoring_status = MonitoringStatus.INACTIVE
                        stats["archived"] += 1
            
            # Сохраняем изменения
            await session.commit()
            
        logger.success(f"Синхронизация завершена: {stats}")
        return stats

    async def sync_from_settings(self) -> str:
        """
        Загружает список файлов из ConfigService и выполняет синхронизацию.
        Возвращает строку со статистикой.
        """
        from services.system import get_config_service
        
        files_list = await get_config_service().get_watched_files()
        
        if not files_list:
            logger.warning("Нет файлов для синхронизации в настройках")
            return "Нет настроенных файлов"
        
        # Синхронизация
        stats = await self.sync_files(files_list)
        return stats
