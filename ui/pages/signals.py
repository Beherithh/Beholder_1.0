import re
from nicegui import ui
from sqlmodel import select, desc, delete
from database.core import get_session
from database.models import Signal, SignalType, MonitoredPair, DelistingEvent
from services.system import get_scraper_service
from ui.layout import create_header

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

    async def toggle_mute_signal(self, signal_id: int):
        """Переключение состояния is_silent у сигнала"""
        async with get_session() as session:
            signal = await session.get(Signal, signal_id)
            if signal:
                signal.is_silent = not signal.is_silent
                session.add(signal)
                await session.commit()
                status = "заглушен" if signal.is_silent else "восстановлен"
                ui.notify(f'Сигнал #{signal_id} {status}', type='info')
        await self.refresh_table()

    async def delete_signal(self, signal_id: int):
        """Удаление сигнала из базы данных"""
        async with get_session() as session:
            signal = await session.get(Signal, signal_id)
            if signal:
                await session.delete(signal)
                await session.commit()
                ui.notify(f'Сигнал #{signal_id} удален', type='positive')
        await self.refresh_table()

    async def resolve_risk_event(self, signal_id: int):
        """
        Полное разрешение события риска для КОНКРЕТНОЙ МОНЕТЫ, связанной с сигналом.
        """
        async with get_session() as session:
            # 1. Найти исходный сигнал и связанную с ним пару
            source_signal_stmt = select(Signal, MonitoredPair).join(MonitoredPair, Signal.pair_id == MonitoredPair.id).where(Signal.id == signal_id)
            result = (await session.execute(source_signal_stmt)).first()
            if not result:
                ui.notify(f'Сигнал #{signal_id} или связанная пара не найдены.', type='negative')
                return
            
            source_signal, source_pair = result
            base_currency = source_pair.symbol.split('/')[0]

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
            ui.notify('Запуск пересчета статусов риска...', type='info')
            scraper_service = get_scraper_service()
            await scraper_service._demote_orphaned_risks(session)
            await session.commit()
            ui.notify('Пересчет статусов завершен.', type='positive')

        # 9. Обновить UI
        await self.refresh_table()

    def get_type_style(self, sig_type: SignalType) -> str:
        """Цветовая индикация для типов сигналов"""
        styles = {
            SignalType.PRICE_CHANGE: 'bg-blue-100 text-blue-800',
            SignalType.VOLUME_ALERT: 'bg-purple-100 text-purple-800',
            SignalType.DELISTING_WARNING: 'bg-red-100 text-red-800 font-bold',
            SignalType.ST_WARNING: 'bg-yellow-100 text-yellow-800',
        }
        return styles.get(sig_type, 'bg-gray-100 text-gray-800')

    async def refresh_table(self):
        """Перезагрузка данных в таблицу"""
        await self.load_signals()
        
        self.full_rows = []
        for s, p in self.signals:
            self.full_rows.append({
                'id': s.id,
                'time': s.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'exchange': p.exchange if p else "-",
                'symbol': p.symbol if p else "-",
                'type': s.type.value,
                'message': s.raw_message,
                'sent': s.is_sent,
                'is_silent': s.is_silent,
                'type_raw': s.type
            })
            
        if self.table:
            self.table.rows[:] = self.full_rows
            self.table.update()

    async def render(self):
        await self.load_signals()
        self.full_rows = []
        for s, p in self.signals:
            self.full_rows.append({
                'id': s.id,
                'time': s.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'exchange': p.exchange if p else "-",
                'symbol': p.symbol if p else "-",
                'type': s.type.value,
                'message': s.raw_message,
                'sent': s.is_sent,
                'is_silent': s.is_silent,
                'type_raw': s.type
            })

        state = { "search_text": "", "filter_exchange": "Все", "filter_type": "Все" }

        def apply_filters():
            filtered = self.full_rows
            if state["filter_exchange"] != 'Все':
                filtered = [r for r in filtered if r['exchange'] == state["filter_exchange"]]
            if state["filter_type"] != 'Все':
                filtered = [r for r in filtered if r['type'] == state["filter_type"]]
            if state["search_text"]:
                search = str(state["search_text"]).lower()
                filtered = [r for r in filtered if search in r['symbol'].lower() or search in r['message'].lower()]
            if self.table:
                self.table.rows = filtered
                self.table.update()

        def reset_filters():
            state["filter_exchange"] = 'Все'
            state["filter_type"] = 'Все'
            state["search_text"] = ''
            apply_filters()

        async def refresh_and_filter():
            await self.refresh_table()
            exchanges = ['Все'] + sorted(list(set(r['exchange'] for r in self.full_rows if r['exchange'] != '-')))
            types = ['Все'] + sorted(list(set(r['type'] for r in self.full_rows)))
            ex_select.options = exchanges
            type_select.options = types
            apply_filters()
            ui.notify('Сигналы обновлены', type='info')
            
        async def handle_toggle_mute(signal_id):
            await self.toggle_mute_signal(signal_id)
            apply_filters()

        async def handle_delete_signal(signal_id):
            await self.delete_signal(signal_id)
            apply_filters()

        async def handle_resolve_event(signal_id):
            await self.resolve_risk_event(signal_id)

        with ui.card().classes('w-full max-w-6xl mx-auto p-4'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('История сигналов (последние 100)').classes('text-2xl font-bold')
                ui.button(icon='refresh', on_click=refresh_and_filter).props('flat dense')

            initial_exchanges = ['Все'] + sorted(list(set(r['exchange'] for r in self.full_rows if r['exchange'] != '-')))
            initial_types = ['Все'] + sorted(list(set(r['type'] for r in self.full_rows)))

            with ui.row().classes('w-full gap-2 items-center mb-4 wrap'):
                search_input = ui.input(placeholder='Поиск пары / текста...', on_change=apply_filters).classes('w-48').props('dense outlined').bind_value(state, 'search_text')
                ex_select = ui.select(initial_exchanges, label='Биржа', on_change=apply_filters).classes('w-32').props('dense outlined').bind_value(state, 'filter_exchange')
                type_select = ui.select(initial_types, label='Тип алерта', on_change=apply_filters).classes('w-40').props('dense outlined').bind_value(state, 'filter_type')
                ui.button(icon='restart_alt', on_click=reset_filters).props('flat round dense')

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
                    <q-badge :class="props.row.type_raw === 'price_change' ? 'bg-blue' : (props.row.type_raw === 'volume_alert' ? 'bg-purple' : (props.row.type_raw === 'delisting_warning' ? 'bg-red' : 'bg-orange'))">
                        {{ props.value }}
                    </q-badge>
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

                    <q-btn flat round dense 
                           :icon="props.row.is_silent ? 'volume_off' : 'volume_up'" 
                           :color="props.row.is_silent ? 'grey' : 'blue'" 
                           @click="$parent.$emit('toggle_mute', props.row.id)">
                        <q-tooltip>{{ props.row.is_silent ? 'Включить звук' : 'Заглушить' }}</q-tooltip>
                    </q-btn>

                    <q-btn flat round dense 
                           icon="delete" 
                           color="red" 
                           @click="$parent.$emit('delete_signal', props.row.id)">
                        <q-tooltip>Удалить сигнал</q-tooltip>
                    </q-btn>
                </q-td>
            ''')
            
            self.table.on('toggle_mute', lambda msg: handle_toggle_mute(msg.args))
            self.table.on('delete_signal', lambda msg: handle_delete_signal(msg.args))
            self.table.on('resolve_event', lambda msg: handle_resolve_event(msg.args))

@ui.page('/signals')
async def signals_page():
    create_header()
    page = SignalsPage()
    await page.render()
