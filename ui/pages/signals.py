import re
from nicegui import ui
from sqlmodel import select, desc, delete
from database.core import get_session
from database.models import Signal, SignalType, MonitoredPair, DelistingEvent
from services.system import services
from ui.layout import create_header

# Глобальное состояние фильтров для сохранения параметров между перезапусками/обновлениями
GLOBAL_SIGNALS_FILTER_STATE = {
    "search_text": "",
    "filter_exchange": "Все",
    "filter_type": "Все"
}

class SignalsPage:
    def __init__(self):
        self.signals = []
        self.full_rows = []
        self.table = None

    async def load_signals(self):
        """Загрузка последних 100 сигналов из БД"""
        async with get_session() as session:
            statement = (
                select(Signal, MonitoredPair)
                .join(MonitoredPair, Signal.pair_id == MonitoredPair.id, isouter=True)
                .order_by(desc(Signal.created_at))
                .limit(100)
            )
            result = await session.execute(statement)
            self.signals = result.all()

    async def resolve_risk_event(self, signal_id: int):
        """
        Полный сброс и удаление событий и сигналов риска для КОНКРЕТНОЙ МОНЕТЫ, связанной с сигналом.
        """
        async with get_session() as session:
            # 1. Найти исходный сигнал и связанную с ним пару
            source_signal_stmt = select(Signal, MonitoredPair).join(MonitoredPair, Signal.pair_id == MonitoredPair.id).where(Signal.id == signal_id)
            result = (await session.execute(source_signal_stmt)).first()
            if not result:
                ui.notify(f'Сигнал #{signal_id} или связанная пара не найдены.', type='negative')
                return
            
            source_signal, source_pair = result
            base_currency = source_pair.base_currency

            # 2. Извлечь URL из сообщения
            url_match = re.search(r'https?://[^\s]+', source_signal.raw_message)
            if not url_match:
                ui.notify('Не удалось извлечь URL из сигнала. Невозможно найти связанное событие.', type='negative')
                return
            event_url = url_match.group(0)

            # 3. Найти DelistingEvent для удаления (по URL и базовой валюте)
            event_stmt = select(DelistingEvent).where(
                DelistingEvent.announcement_url == event_url,
                DelistingEvent.symbol == base_currency
            )
            events_to_delete = (await session.execute(event_stmt)).scalars().all()

            if not events_to_delete:
                ui.notify(f'Событие риска для {base_currency} с URL {event_url} не найдено.', type='warning')
                return

            # 4. Найти все ID пар для этой базовой валюты
            pairs_for_currency_stmt = select(MonitoredPair.id).where(MonitoredPair.symbol.like(f'{base_currency}/%'))
            pair_ids_for_currency = (await session.execute(pairs_for_currency_stmt)).scalars().all()

            # 5. Найти все сигналы для удаления (по ID пар и URL)
            signals_to_delete_stmt = select(Signal).where(
                Signal.pair_id.in_(pair_ids_for_currency),
                Signal.raw_message.like(f'%{event_url}%')
            )
            signals_to_delete = (await session.execute(signals_to_delete_stmt)).scalars().all()

            # 6. Показать диалог подтверждения
            with ui.dialog() as dialog, ui.card():
                ui.label(f'Разрешить событие риска для монеты {base_currency}?').classes('text-lg font-bold')
                ui.label(f'URL: {event_url}')
                ui.label(f'Будет удалено событий: {len(events_to_delete)}')
                ui.label(f'Будет удалено связанных сигналов: {len(signals_to_delete)}')
                with ui.row().classes('mt-4'):
                    ui.button('Да, разрешить', on_click=lambda: dialog.submit(True), color='red')
                    ui.button('Отмена', on_click=lambda: dialog.submit(False))
            
            confirmed = await dialog
            if not confirmed:
                return

            # 7. Выполнить удаление
            for event in events_to_delete:
                await session.delete(event)
            for sig in signals_to_delete:
                await session.delete(sig)
            
            await session.commit()
            ui.notify(f'Событие для {base_currency} и {len(signals_to_delete)} сигналов удалены.', type='positive')

            # 8. Запустить пересчет статусов
            # demote_orphaned_risks сам выполняет commit() внутри
            ui.notify('Запуск пересмотра статусов риска...', type='info')
            await services.scraper.demote_orphaned_risks(session)
            ui.notify('Пересмотр статусов завершен.', type='positive')

        # 9. Обновить UI
        await self.refresh_table()

    def get_type_style(self, sig_type: SignalType) -> str:
        """Цветовая индикация для типов сигналов"""
        styles = {
            SignalType.PRICE_CHANGE: 'bg-blue-100 text-blue-800',
            SignalType.VOLUME_ALERT: 'bg-purple-100 text-purple-800',
            SignalType.DELISTING_WARNING: 'bg-red-100 text-red-800 font-bold',
            SignalType.ST_WARNING: 'bg-yellow-100 text-yellow-800',
            SignalType.RANK_WARNING: 'bg-orange-100 text-orange-800',
        }
        return styles.get(sig_type, 'bg-gray-100 text-gray-800')

    async def refresh_table(self):
        """Перезагрузка данных в таблицу"""
        await self.load_signals()
        
        self.full_rows = []
        for s, p in self.signals:
            url_match = re.search(r'https?://[^\s]+', s.raw_message)
            announcement_url = url_match.group(0) if url_match else None
            
            self.full_rows.append({
                'id': s.id,
                'time': s.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'exchange': p.exchange if p else "-",
                'symbol': p.symbol if p else "-",
                'type': s.type.value,
                'message': s.raw_message,
                'sent': s.is_sent,
                'type_raw': s.type,
                'announcement_url': announcement_url
            })
            
            
        if self.table:
            self.apply_filters()

    def apply_filters(self):
        filtered = self.full_rows
        if GLOBAL_SIGNALS_FILTER_STATE["filter_exchange"] != 'Все':
            filtered = [r for r in filtered if r['exchange'] == GLOBAL_SIGNALS_FILTER_STATE["filter_exchange"]]
        if GLOBAL_SIGNALS_FILTER_STATE["filter_type"] != 'Все':
            filtered = [r for r in filtered if r['type'] == GLOBAL_SIGNALS_FILTER_STATE["filter_type"]]
        if GLOBAL_SIGNALS_FILTER_STATE["search_text"]:
            search = str(GLOBAL_SIGNALS_FILTER_STATE["search_text"]).lower()
            filtered = [r for r in filtered if search in r['symbol'].lower() or search in r['message'].lower()]
        if self.table:
            self.table.rows = filtered
            self.table.update()

    def reset_filters(self):
        GLOBAL_SIGNALS_FILTER_STATE["filter_exchange"] = 'Все'
        GLOBAL_SIGNALS_FILTER_STATE["filter_type"] = 'Все'
        GLOBAL_SIGNALS_FILTER_STATE["search_text"] = ''
        self.apply_filters()

    async def render(self):
        await self.load_signals()
        self.full_rows = []
        for s, p in self.signals:
            url_match = re.search(r'https?://[^\s]+', s.raw_message)
            announcement_url = url_match.group(0) if url_match else None
            
            self.full_rows.append({
                'id': s.id,
                'time': s.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'exchange': p.exchange if p else "-",
                'symbol': p.symbol if p else "-",
                'type': s.type.value,
                'message': s.raw_message,
                'sent': s.is_sent,
                'type_raw': s.type,
                'announcement_url': announcement_url
            })

        async def refresh_and_filter():
            await self.refresh_table()
            exchanges = ['Все'] + sorted(list(set(r['exchange'] for r in self.full_rows if r['exchange'] != '-')))
            types = ['Все'] + sorted(list(set(r['type'] for r in self.full_rows)))
            ex_select.options = exchanges
            ex_select.update()
            type_select.options = types
            type_select.update()
            self.apply_filters()
            ui.notify('Сигналы обновлены', type='info')
            
        async def handle_resolve_event(signal_id):
            await self.resolve_risk_event(signal_id)

        with ui.card().classes('w-full max-w-6xl mx-auto p-4'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('История сигналов (последние 100)').classes('text-2xl font-bold')
                ui.button(icon='refresh', on_click=refresh_and_filter).props('flat dense')

            initial_exchanges = ['Все'] + sorted(list(set(r['exchange'] for r in self.full_rows if r['exchange'] != '-')))
            if GLOBAL_SIGNALS_FILTER_STATE['filter_exchange'] not in initial_exchanges:
                initial_exchanges.append(GLOBAL_SIGNALS_FILTER_STATE['filter_exchange'])
                
            initial_types = ['Все'] + sorted(list(set(r['type'] for r in self.full_rows)))
            if GLOBAL_SIGNALS_FILTER_STATE['filter_type'] not in initial_types:
                initial_types.append(GLOBAL_SIGNALS_FILTER_STATE['filter_type'])

            with ui.row().classes('w-full gap-2 items-center mb-4 wrap'):
                search_input = ui.input(placeholder='Поиск пары / текста...', on_change=lambda e: self.apply_filters()).classes('w-48').props('dense outlined').bind_value(GLOBAL_SIGNALS_FILTER_STATE, 'search_text')
                ex_select = ui.select(options=initial_exchanges, value=GLOBAL_SIGNALS_FILTER_STATE['filter_exchange'], label='Биржа', on_change=lambda e: self.apply_filters()).classes('w-32').props('dense outlined').bind_value(GLOBAL_SIGNALS_FILTER_STATE, 'filter_exchange')
                type_select = ui.select(options=initial_types, value=GLOBAL_SIGNALS_FILTER_STATE['filter_type'], label='Тип алерта', on_change=lambda e: self.apply_filters()).classes('w-40').props('dense outlined').bind_value(GLOBAL_SIGNALS_FILTER_STATE, 'filter_type')
                ui.button(icon='restart_alt', on_click=self.reset_filters).props('flat round dense')

            columns = [
                {'name': 'time', 'label': 'Время', 'field': 'time', 'sortable': True, 'align': 'left'},
                {'name': 'exchange', 'label': 'Биржа', 'field': 'exchange', 'sortable': True, 'align': 'center'},
                {'name': 'symbol', 'label': 'Пара', 'field': 'symbol', 'sortable': True, 'align': 'center'},
                {'name': 'type', 'label': 'Тип', 'field': 'type', 'sortable': True, 'align': 'center'},
                {'name': 'sent', 'label': 'Отправлен', 'field': 'sent', 'sortable': True, 'align': 'center'},
                {'name': 'message', 'label': 'Сообщение', 'field': 'message', 'align': 'left', 'classes': 'whitespace-pre-line max-w-xs break-words'},
                {'name': 'actions', 'label': 'Действия', 'field': 'id', 'align': 'center'},
            ]

            self.table = ui.table(columns=columns, rows=self.full_rows, row_key='id').classes('w-full').props('flat bordered wrap-cells')
            
            self.table.add_slot('body-cell-type', '''
                <q-td :props="props">
                    <template v-if="props.row.announcement_url">
                        <a :href="props.row.announcement_url" target="_blank" class="no-underline">
                            <q-badge :class="props.row.type_raw === 'price_change' ? 'bg-blue' : (props.row.type_raw === 'volume_alert' ? 'bg-purple' : (props.row.type_raw === 'delisting_warning' ? 'bg-red' : 'bg-orange'))" class="cursor-pointer hover:bg-gray-200">
                                {{ props.value }}
                                <q-icon name="open_in_new" size="xs" class="q-ml-xs" />
                            </q-badge>
                        </a>
                    </template>
                    <template v-else>
                        <q-badge :class="props.row.type_raw === 'price_change' ? 'bg-blue' : (props.row.type_raw === 'volume_alert' ? 'bg-purple' : (props.row.type_raw === 'delisting_warning' ? 'bg-red' : 'bg-orange'))">
                            {{ props.value }}
                        </q-badge>
                    </template>
                </q-td>
            ''')

            self.table.add_slot('body-cell-sent', '''
                <q-td :props="props">
                    <q-icon name="check" color="green" v-if="props.value" />
                    <q-icon name="close" color="red" v-else />
                </q-td>
            ''')

            self.table.add_slot('body-cell-actions', '''
                <q-td :props="props" class="flex flex-nowrap gap-1 justify-center items-center h-full pt-3">
                    <q-btn flat round dense 
                           v-if="props.row.type_raw === 'delisting_warning' || props.row.type_raw === 'st_warning'"
                           icon="task_alt" 
                           color="green" 
                           @click="$parent.$emit('resolve_event', props.row.id)">
                        <q-tooltip>Разрешить событие риска</q-tooltip>
                    </q-btn>
                </q-td>
            ''')
            
            self.table.on('resolve_event', lambda msg: handle_resolve_event(msg.args))

        # Применяем фильтры сразу после отрисовки таблицы, 
        # чтобы данные соответствовали сохраненному GLOBAL_SIGNALS_FILTER_STATE
        self.apply_filters()

@ui.page('/signals')
async def signals_page():
    create_header()
    page = SignalsPage()
    await page.render()
