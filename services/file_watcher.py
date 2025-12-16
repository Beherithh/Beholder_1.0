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
        "GATE": "gateio",
        # Сюда можно добавлять другие биржи по мере необходимости
    }

    async def _read_files(self, file_paths: List[str]) -> Set[Tuple[str, str, str]]:
        """
        Приватный метод. Читает все файлы JSON и собирает уникальные пары.
        Формат имени файла: "Gate_instruments_USDT.txt" или "4_Gate_instruments_USDT.txt".
        Формат содержимого: JSON {"listHelper": [{"symbol": "BTC_USDT", ...}, ...]}
        
        Возвращает множество кортежей: {(exchange, symbol, source_file_path), ...}
        """
        
        found_pairs = set()

        for path_str in file_paths:
            path = Path(path_str)
            if not path.exists():
                logger.warning(f"Файл не найден: {path_str}")
                continue

            # 1. Парсинг имени файла
            filename = path.name
            
            # Регулярка для извлечения биржи и валюты котирования (например, USDT).
            # Имя: "Gate_instruments_USDT.txt" -> Exchange: GATE, Quote: USDT
            match = re.search(r'(?:^\d+_)?(.+?)_instruments_([^\.]+)', filename)
            
            if match:
                raw_exchange = match.group(1).upper()
                # Нормализация имени биржи (например GATE -> gateio)
                exchange_name = self.EXCHANGE_MAPPING.get(raw_exchange, raw_exchange)
                
                quote_currency = match.group(2).upper() # Например "USDT"
            else:
                logger.warning(f"Не удалось извлечь название биржи и валюты из имени файла: {filename}. Используем defaults.")
                exchange_name = "UNKNOWN"
                quote_currency = ""

            # 2. Парсинг содержимого
            try:
                content = path.read_text(encoding='utf-8')
                data = json.loads(content)
                
                # Ищем список пар в ключе "listHelper"
                items = data.get("listHelper", [])
                
                if not items:
                    logger.warning(f"В файле {filename} не найден ключ 'listHelper' или он пуст.")
                    continue

                for item in items:
                    raw_symbol = item.get("symbol", "")
                    if raw_symbol:
                        # Нормализация символа
                        # 1. Если есть разделитель "_" -> меняем на "/" (BTC_USDT -> BTC/USDT)
                        if "_" in raw_symbol:
                            normalized_symbol = raw_symbol.replace("_", "/").upper()
                        # 2. Если разделителя нет (BTCUSDT), пробуем отделить quote_currency (USDT)
                        elif quote_currency and raw_symbol.endswith(quote_currency) and raw_symbol != quote_currency:
                            base = raw_symbol[:-len(quote_currency)]
                            normalized_symbol = f"{base}/{quote_currency}".upper()
                        # 3. Иначе оставляем как есть
                        else:
                            normalized_symbol = raw_symbol.upper()
                        
                        found_pairs.add((exchange_name, normalized_symbol, path_str))
                    
            except json.JSONDecodeError:
                logger.error(f"Ошибка парсинга JSON в файле {path_str}")
            except Exception as e:
                logger.error(f"Ошибка при чтении файла {path_str}: {e}")

        logger.info(f"Всего найдено пар в файлах: {len(found_pairs)}")
        return found_pairs

    async def sync_files(self, file_paths: List[str]) -> Dict[str, int]:
        """
        Основной метод синхронизации.
        1. Читает файлы.
        2. Загружает текущее состояние базы.
        3. Добавляет новые, обновляет существующие, помечает удаленные как INACTIVE.
        """
        stats = {"added": 0, "reactivated": 0, "archived": 0, "unchanged": 0}
        
        # 1. Читаем файлы
        file_pairs_set = await self._read_files(file_paths)
        # Создаем словарь для быстрого поиска: (exchange, symbol) -> source_file
        # Внимание: если пара есть в двух файлах, победит последний. Это упрощение.
        incoming_pairs_map = { (ex, sym): src for (ex, sym, src) in file_pairs_set }

        async with self.session_factory() as session:  # type: AsyncSession
            # 2. Получаем все пары из БД (и активные, и неактивные)
            statement = select(MonitoredPair)
            result = await session.execute(statement)
            db_pairs = result.scalars().all()
            
            # Превращаем в словарь: (exchange, symbol) -> MonitoredPair объект
            db_pairs_map = { (p.exchange, p.symbol): p for p in db_pairs }

            # 3. Обработка ВХОДЯЩИХ (Новые или Реактивация)
            for (exchange, symbol), source_file in incoming_pairs_map.items():
                if (exchange, symbol) in db_pairs_map:
                    # Пара уже есть в базе
                    existing_pair = db_pairs_map[(exchange, symbol)]
                    
                    if existing_pair.monitoring_status == MonitoringStatus.INACTIVE:
                        # Если была удалена, а теперь вернулась -> ВОССТАНАВЛИВАЕМ
                        existing_pair.monitoring_status = MonitoringStatus.ACTIVE
                        existing_pair.source_file = source_file # Обновляем путь файла, если изменился
                        stats["reactivated"] += 1
                    else:
                        # Она и так активна
                        stats["unchanged"] += 1
                        # Обновим source_file на всякий случай, если переместили в другой файл
                        if existing_pair.source_file != source_file:
                            existing_pair.source_file = source_file
                else:
                    # Пары нет в базе -> СОЗДАЕМ
                    new_pair = MonitoredPair(
                        exchange=exchange,
                        symbol=symbol,
                        source_file=source_file,
                        monitoring_status=MonitoringStatus.ACTIVE,
                        risk_level=RiskLevel.NORMAL
                    )
                    session.add(new_pair)
                    stats["added"] += 1

            # 4. Обработка ИСЧЕЗНУВШИХ (Soft Delete)
            # Проходим по тем, кто В БАЗЕ, но НЕТ во входящих
            for (exchange, symbol), db_pair in db_pairs_map.items():
                if (exchange, symbol) not in incoming_pairs_map:
                    if db_pair.monitoring_status == MonitoringStatus.ACTIVE:
                        # Была активна, но исчезла из файлов -> УДАЛЯЕМ (Мягко)
                        db_pair.monitoring_status = MonitoringStatus.INACTIVE
                        stats["archived"] += 1
            
            # Сохраняем изменения
            await session.commit()
            
        logger.success(f"Синхронизация завершена: {stats}")
        return stats
