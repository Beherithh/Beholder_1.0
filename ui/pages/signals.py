from nicegui import ui
from sqlmodel import select, desc, delete
from database.core import get_session
from database.models import Signal, SignalType, MonitoredPair
from ui.layout import create_header

class SignalsPage:
    def __init__(self):
        self.signals = []
        self.full_rows = []
        self.table = None

    async def load_signals(self):
        """Загрузка последних 100 сигналов из БД"""
        async with get_session() as session:
            # JOIN: signals and their optional pairs
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
        # Обновляем данные и UI
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
                'type_raw': s.type # Для кастомного рендеринга
            })
            
        if self.table:
            # Если вызывается напрямую без фильтров, не применяем их, 
            # но лучше переопределить эту логику через apply_filters (см. ниже)
            self.table.rows[:] = self.full_rows
            self.table.update()

    async def render(self):
        # Первичная загрузка и подготовка rows
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

        state = {
            "search_text": "",
            "filter_exchange": "Все",
            "filter_type": "Все",
        }

        def apply_filters():
            filtered = self.full_rows
            
            if state["filter_exchange"] != 'Все':
                filtered = [r for r in filtered if r['exchange'] == state["filter_exchange"]]
                
            if state["filter_type"] != 'Все':
                filtered = [r for r in filtered if r['type'] == state["filter_type"]]
                
            if state["search_text"]:
                search = state["search_text"].lower()
                filtered = [r for r in filtered if search in r['symbol'].lower() or search in r['message'].lower()]

            if self.table:
                self.table.rows = filtered

        def reset_filters():
            state["filter_exchange"] = 'Все'
            state["filter_type"] = 'Все'
            state["search_text"] = ''
            
            ex_select.value = 'Все'
            type_select.value = 'Все'
            search_input.value = ''
            
            apply_filters()

        async def refresh_and_filter():
            await self.refresh_table()
            
            # Обновляем опции дропдаунов
            exchanges = ['Все'] + sorted(list(set(r['exchange'] for r in self.full_rows if r['exchange'] != '-')))
            types = ['Все'] + sorted(list(set(r['type'] for r in self.full_rows)))
            
            ex_select.options = exchanges
            type_select.options = types
            
            apply_filters()
            ui.notify('Сигналы обновлены', type='info')
            
        # Кастомный метод toggle, обновляющий фильтры после сохранения
        async def handle_toggle_mute(signal_id):
            await self.toggle_mute_signal(signal_id)
            apply_filters()

        with ui.card().classes('w-full max-w-6xl mx-auto p-4'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('История сигналов (последние 100)').classes('text-2xl font-bold')
                ui.button(icon='refresh', on_click=refresh_and_filter).props('flat dense')

            # --- Панель фильтров ---
            initial_exchanges = ['Все'] + sorted(list(set(r['exchange'] for r in self.full_rows if r['exchange'] != '-')))
            initial_types = ['Все'] + sorted(list(set(r['type'] for r in self.full_rows)))

            with ui.row().classes('w-full gap-2 items-center mb-4 wrap'):
                search_input = ui.input(placeholder='Поиск пары / текста...').classes('w-48').props('dense outlined')
                search_input.on('update:model-value', lambda e: [state.update({"search_text": e.args}), apply_filters()])

                ex_select = ui.select(initial_exchanges, label='Биржа', value='Все').classes('w-32').props('dense outlined')
                ex_select.on('update:model-value', lambda e: [state.update({"filter_exchange": e.args}), apply_filters()])

                type_select = ui.select(initial_types, label='Тип алерта', value='Все').classes('w-40').props('dense outlined')
                type_select.on('update:model-value', lambda e: [state.update({"filter_type": e.args}), apply_filters()])
                
                ui.button(icon='restart_alt', on_click=reset_filters).props('flat round dense')

            columns = [
                {'name': 'time', 'label': 'Время', 'field': 'time', 'sortable': True, 'align': 'left'},
                {'name': 'exchange', 'label': 'Биржа', 'field': 'exchange', 'sortable': True, 'align': 'center'},
                {'name': 'symbol', 'label': 'Пара', 'field': 'symbol', 'sortable': True, 'align': 'center'},
                {'name': 'type', 'label': 'Тип', 'field': 'type', 'sortable': True, 'align': 'center'},
                {'name': 'sent', 'label': 'Отправлен', 'field': 'sent', 'sortable': True, 'align': 'center'},
                {'name': 'message', 'label': 'Сообщение', 'field': 'message', 'align': 'left', 'classes': 'whitespace-pre-line max-w-md break-words'},
                {'name': 'actions', 'label': 'Заглушить', 'field': 'id', 'align': 'center'},
            ]

            self.table = ui.table(columns=columns, rows=self.full_rows, row_key='id').classes('w-full').props('flat bordered wrap-cells')
            
            # Кастомизация столбца Тип
            self.table.add_slot('body-cell-type', '''
                <q-td :props="props">
                    <q-badge :class="props.row.type_raw === 'price_change' ? 'bg-blue' : (props.row.type_raw === 'volume_alert' ? 'bg-purple' : (props.row.type_raw === 'delisting_warning' ? 'bg-red' : 'bg-orange'))">
                        {{ props.value }}
                    </q-badge>
                </q-td>
            ''')

            # Slot for sent status
            self.table.add_slot('body-cell-sent', '''
                <q-td :props="props">
                    <q-icon name="check" color="green" v-if="props.value" />
                    <q-icon name="close" color="red" v-else />
                </q-td>
            ''')

            # Кастомизация столбца Действия (Заглушить)
            self.table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat round dense 
                           :icon="props.row.is_silent ? 'volume_off' : 'volume_up'" 
                           :color="props.row.is_silent ? 'grey' : 'blue'" 
                           @click="$parent.$emit('toggle_mute', props.row.id)" />
                </q-td>
            ''')
            
            # Обработка события нажатия из JS
            self.table.on('toggle_mute', lambda msg: handle_toggle_mute(msg.args))

@ui.page('/signals')
async def signals_page():
    create_header()
    page = SignalsPage()
    await page.render()
