import json
from datetime import datetime
from typing import List, Dict, Any

from sqlmodel import select, func
from nicegui import ui

from database.core import get_session
from database.models import MonitoredPair, MarketData, RiskLevel, MonitoringStatus, DelistingEvent
from ui.layout import create_header

class DashboardPage:
    def __init__(self):
        self.table: ui.table = None
        self.stats_cards: Dict[str, ui.label] = {}
        self.full_data: List[Dict[str, Any]] = []
        
        # Компоненты фильтров
        self.ex_select: ui.select = None
        self.st_select: ui.select = None
        
        # Значения фильтров
        self.filter_exchange = 'Все'
        self.filter_status = 'Все'
        self.search_text = ''

    async def get_dashboard_data(self) -> Dict[str, Any]:
        """
        Собирает данные для таблицы и статистику.
        """
        async with get_session() as session:
            # 1. Получаем все активные пары
            stmt = select(MonitoredPair).where(MonitoredPair.monitoring_status == MonitoringStatus.ACTIVE)
            pairs = (await session.execute(stmt)).scalars().all()
            
            data_rows = []
            stats = {"total": len(pairs), "risk": 0, "delist": 0}
            
            for pair in pairs:
                # Статистика
                if pair.risk_level == RiskLevel.DELISTING_PLANNED:
                    stats["delist"] += 1
                elif pair.risk_level != RiskLevel.NORMAL:
                    stats["risk"] += 1

                # Ищем последнюю цену
                price_stmt = select(MarketData).where(MarketData.pair_id == pair.id).order_by(MarketData.timestamp.desc()).limit(1)
                last_price = (await session.execute(price_stmt)).scalar_one_or_none()
                
                # Распаковка labels
                try:
                    labels = json.loads(pair.source_label) if pair.source_label else []
                    labels_str = ", ".join(labels) if isinstance(labels, list) else str(pair.source_label)
                except:
                    labels_str = pair.source_label or ""

                price_val = last_price.close if last_price else 0.0
                updated_at = last_price.timestamp.strftime("%d.%m %H:%M") if last_price else "—"
                
                # Поиск ссылки на статью для рисковых статусов
                announcement_url = None
                if pair.risk_level != RiskLevel.NORMAL:
                    base_currency = pair.symbol.split('/')[0]
                    # Ищем последнее событие для этой монеты
                    # Если это CROSS-риск, нам все равно нужна ссылка на то событие, которое его вызвало
                    event_stmt = select(DelistingEvent).where(
                        DelistingEvent.symbol == base_currency
                    ).order_by(DelistingEvent.found_at.desc()).limit(1)
                    
                    event = (await session.execute(event_stmt)).scalar_one_or_none()
                    if event:
                        announcement_url = event.announcement_url

                # Цвета для статуса риска
                risk_color = "text-gray-400"
                if pair.risk_level == RiskLevel.DELISTING_PLANNED:
                    risk_color = "text-red-600 font-bold"
                elif pair.risk_level in [RiskLevel.RISK_ZONE, RiskLevel.CROSS_DELISTING]:
                    risk_color = "text-orange-600 font-bold"
                elif pair.risk_level in [RiskLevel.CROSS_RISK]:
                    risk_color = "text-yellow-600 font-medium"

                data_rows.append({
                    "id": pair.id,
                    "exchange": pair.exchange,
                    "symbol": pair.symbol,
                    "price": f"{price_val:.8f}".rstrip('0').rstrip('.') if price_val > 0 else "N/A",
                    "risk_level": pair.risk_level.value.upper().replace("_", " "),
                    "risk_color": risk_color,
                    "announcement_url": announcement_url,
                    "labels": labels_str,
                    "labels_count": len(labels) if isinstance(labels, list) else 1,
                    "updated": updated_at
                })
            
            return {"rows": data_rows, "stats": stats}

    def create(self):
        @ui.page('/')
        async def main_page():
            create_header()
            
            initial_data = await self.get_dashboard_data()
            self.full_data = initial_data["rows"]
            stats = initial_data["stats"]

            with ui.column().classes('w-full p-6 bg-gray-50 min-h-screen'):
                # Секция статистики
                with ui.row().classes('w-full gap-4 mb-6'):
                    with ui.card().classes('flex-1 p-4 bg-white shadow-sm border-l-4 border-blue-500'):
                        ui.label('Всего пар').classes('text-sm text-gray-500 uppercase')
                        self.stats_cards['total'] = ui.label(str(stats['total'])).classes('text-3xl font-bold')
                    
                    with ui.card().classes('flex-1 p-4 bg-white shadow-sm border-l-4 border-orange-500'):
                        ui.label('В зоне риска (ST)').classes('text-sm text-gray-500 uppercase')
                        self.stats_cards['risk'] = ui.label(str(stats['risk'])).classes('text-3xl font-bold text-orange-600')

                    with ui.card().classes('flex-1 p-4 bg-white shadow-sm border-l-4 border-red-500'):
                        ui.label('Делистинг скоро').classes('text-sm text-gray-500 uppercase')
                        self.stats_cards['delist'] = ui.label(str(stats['delist'])).classes('text-3xl font-bold text-red-600')

                # Секция фильтров
                with ui.card().classes('w-full bg-white shadow-md mb-4 p-4'):
                    with ui.row().classes('w-full items-center gap-4'):
                        ui.icon('filter_alt').classes('text-gray-400 text-xl')
                        
                        # Поиск
                        search_input = ui.input(placeholder='Поиск (BTC, MEXC, Метка...)').classes('flex-grow')
                        search_input.bind_value(self, 'search_text')
                        search_input.on('update:model-value', self.apply_filters)

                        # Фильтр по Бирже
                        exchanges = self._get_unique_exchanges()
                        self.ex_select = ui.select(exchanges, label='Биржа', value='Все').classes('w-40')
                        self.ex_select.bind_value(self, 'filter_exchange')
                        self.ex_select.on('update:model-value', self.apply_filters)

                        # Фильтр по Статусу
                        statuses = self._get_unique_statuses()
                        self.st_select = ui.select(statuses, label='Статус', value='Все').classes('w-48')
                        self.st_select.bind_value(self, 'filter_status')
                        self.st_select.on('update:model-value', self.apply_filters)
                        
                        ui.button(icon='restart_alt', on_click=self.reset_filters).props('flat round gray')

                # Таблица данных
                with ui.card().classes('w-full bg-white shadow-md'):
                    ui.label('Мониторинг активов').classes('text-xl font-bold p-4 border-b w-full')
                    
                    columns = [
                        {'name': 'exchange', 'label': 'Биржа', 'field': 'exchange', 'align': 'left', 'sortable': True},
                        {'name': 'symbol', 'label': 'Пара', 'field': 'symbol', 'align': 'left', 'sortable': True},
                        {'name': 'price', 'label': 'Цена', 'field': 'price', 'align': 'right', 'sortable': True},
                        {'name': 'risk_level', 'label': 'Статус', 'field': 'risk_level', 'align': 'center', 'sortable': True},
                        {'name': 'labels_count', 'label': 'Кол-во списков', 'field': 'labels_count', 'align': 'center', 'sortable': True},
                        {'name': 'labels', 'label': 'Метки файлов', 'field': 'labels', 'align': 'left', 'sortable': True},
                        {'name': 'updated', 'label': 'Обновлено', 'field': 'updated', 'align': 'right'},
                    ]

                    self.table = ui.table(columns=columns, rows=self.full_data, row_key='id').classes('w-full sticky-header')
                    self.table.props('flat bordered wrap-cells')
                    
                    # Привязываем глобальный фильтр NiceGUI к нашему полю поиска для дополнительной гибкости
                    self.table.bind_filter_from(self, 'search_text')
                    
                    # Кастомная отрисовка для колонки Риска (цвета + ссылка)
                    self.table.add_slot('body-cell-risk_level', '''
                        <q-td :props="props">
                            <template v-if="props.row.announcement_url">
                                <a :href="props.row.announcement_url" target="_blank" class="no-underline">
                                    <q-badge :class="props.row.risk_color" outline class="cursor-pointer hover:bg-gray-100">
                                        {{ props.value }}
                                        <q-icon name="open_in_new" size="xs" class="q-ml-xs" />
                                    </q-badge>
                                </a>
                            </template>
                            <template v-else>
                                <q-badge :class="props.row.risk_color" outline>
                                    {{ props.value }}
                                </q-badge>
                            </template>
                        </q-td>
                    ''')

                # Кнопка ручного обновления
                ui.button('Обновить данные', on_click=self.refresh_table, icon='refresh').props('rounded outline').classes('mt-4')

        @ui.page('/dashboard') # Алиас если нужно
        async def dashboard_alias():
            await main_page()

    async def refresh_table(self):
        data = await self.get_dashboard_data()
        self.full_data = data["rows"]
        self.apply_filters()
        
        # Обновляем опции в дропдаунах (если появились новые биржи/статусы)
        if self.ex_select:
            self.ex_select.options = self._get_unique_exchanges()
            self.ex_select.update()
        if self.st_select:
            self.st_select.options = self._get_unique_statuses()
            self.st_select.update()

        # Обновляем цифры в карточках
        self.stats_cards['total'].text = str(data["stats"]['total'])
        self.stats_cards['risk'].text = str(data["stats"]['risk'])
        self.stats_cards['delist'].text = str(data["stats"]['delist'])
        
        ui.notify('Данные обновлены', type='info')

    def apply_filters(self):
        """Применяет выбранные фильтры к данным таблицы."""
        filtered = self.full_data
        
        if self.filter_exchange != 'Все':
            filtered = [r for r in filtered if r['exchange'] == self.filter_exchange]
            
        if self.filter_status == 'Все кроме NORMAL':
            filtered = [r for r in filtered if r['risk_level'] != 'NORMAL']
        elif self.filter_status != 'Все':
            filtered = [r for r in filtered if r['risk_level'] == self.filter_status]
            
        self.table.rows = filtered

    def reset_filters(self):
        self.filter_exchange = 'Все'
        self.filter_status = 'Все'
        self.search_text = ''
        self.apply_filters()

    def _get_unique_exchanges(self) -> List[str]:
        return ['Все'] + sorted(list(set(r['exchange'] for r in self.full_data)))

    def _get_unique_statuses(self) -> List[str]:
        statuses = sorted(list(set(r['risk_level'] for r in self.full_data)))
        return ['Все', 'Все кроме NORMAL'] + statuses

# Инициализируем при импорте
dashboard_page = DashboardPage()
dashboard_page.create()
