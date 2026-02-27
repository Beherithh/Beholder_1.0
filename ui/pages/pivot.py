import json
from typing import List, Dict, Any
from sqlmodel import select
from nicegui import ui

from database.core import get_session
from database.models import MonitoredPair, MonitoringStatus
from ui.layout import create_header

async def get_pivot_data() -> List[Dict[str, Any]]:
    """
    Агрегирует данные из БД по названию монеты.
    Собирает уникальные метки и CMC Rank.
    """
    async with get_session() as session:
        # Получаем все активные пары
        stmt = select(MonitoredPair).where(MonitoredPair.monitoring_status == MonitoringStatus.ACTIVE)
        pairs = (await session.execute(stmt)).scalars().all()
        
        # Словарь для агрегации: { "BTC": {"rank": 1, "labels": set()} }
        aggregated: Dict[str, Dict[str, Any]] = {}

        for pair in pairs:
            # Извлекаем название монеты (до слеша, например BTC из BTC/USDT)
            coin_name = pair.symbol.split('/')[0] if '/' in pair.symbol else pair.symbol
            
            if coin_name not in aggregated:
                aggregated[coin_name] = {
                    "coin": coin_name,
                    "rank": pair.cmc_rank,
                    "labels": set()
                }
            
            # Обработка меток (распаковка JSON списка или чтение строки)
            if pair.source_label:
                try:
                    labels = json.loads(pair.source_label)
                    if isinstance(labels, list):
                        for l in labels: aggregated[coin_name]["labels"].add(l)
                    else:
                        aggregated[coin_name]["labels"].add(str(labels))
                except:
                    aggregated[coin_name]["labels"].add(pair.source_label)

        # Формируем финальный список строк для таблицы
        rows = []
        for coin, data in aggregated.items():
            labels_list = sorted(list(data["labels"]))
            rows.append({
                "coin": coin,
                "rank": data["rank"] if data["rank"] is not None else 999999,
                "labels": ", ".join(labels_list),
                "labels_count": len(labels_list)
            })
            
        return rows

@ui.page('/pivot')
async def pivot_page():
    create_header()
    
    with ui.column().classes('w-full p-4 bg-gray-50 min-h-screen gap-4'):
        with ui.row().classes('w-full items-center justify-between'):
            ui.label('Pivot Table').classes('text-2xl font-bold')
            ui.button('Обновить', icon='refresh', on_click=lambda: ui.navigate.to('/pivot')).props('outline')
        
        columns = [
            {'name': 'coin', 'label': 'Монета', 'field': 'coin', 'align': 'left', 'sortable': True},
            {'name': 'rank', 'label': 'CMC Rank', 'field': 'rank', 'align': 'center', 'sortable': True},
            {'name': 'labels', 'label': 'Метки файлов', 'field': 'labels', 'align': 'left', 'sortable': True},
            {'name': 'labels_count', 'label': 'Кол-во списков', 'field': 'labels_count', 'align': 'center', 'sortable': True},
        ]

        # Загружаем данные
        rows = await get_pivot_data()
        
        with ui.card().classes('w-full bg-white shadow-md'):
            table = ui.table(columns=columns, rows=rows, row_key='coin').classes('w-full')
            table.props('flat bordered wrap-cells')
            
            # Кастомный рендеринг для Rank (чтобы 999999 выглядел как прочерк)
            table.add_slot('body-cell-rank', '''
                <q-td :props="props">
                    <q-badge v-if="props.value < 999999" color="blue-1" text-color="blue-9" class="font-bold">
                        {{ props.value }}
                    </q-badge>
                    <span v-else class="text-gray-300">—</span>
                </q-td>
            ''')

