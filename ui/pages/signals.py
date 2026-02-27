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

    async def delete_signal(self, signal_id: int):
        """Удаление сигнала из базы данных"""
        async with get_session() as session:
            signal = await session.get(Signal, signal_id)
            if signal:
                await session.delete(signal)
                await session.commit()
                ui.notify(f'Сигнал #{signal_id} удален', type='positive')
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
            # Обновляем базовый набор строк
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

        # Состояние фильтров
        state = {
            "search_text": "",
            "filter_exchange": "Все",
            "filter_type": "Все",
        }

        def apply_filters():
            """Применение фильтров к таблице"""
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
            """Сброс всех фильтров в исходное состояние"""
            state["filter_exchange"] = 'Все'
            state["filter_type"] = 'Все'
            state["search_text"] = ''
            
            # Благодаря bind_value, UI обновится автоматически при изменении state
            # Но для надежности вызываем apply_filters
            apply_filters()

        async def refresh_and_filter():
            """Полное обновление данных с сохранением фильтрации"""
            await self.refresh_table()
            
            # Обновляем опции дропдаунов на основе новых данных
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

        with ui.card().classes('w-full max-w-6xl mx-auto p-4'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('История сигналов (последние 100)').classes('text-2xl font-bold')
                ui.button(icon='refresh', on_click=refresh_and_filter).props('flat dense')

            # --- Панель фильтров ---
            initial_exchanges = ['Все'] + sorted(list(set(r['exchange'] for r in self.full_rows if r['exchange'] != '-')))
            initial_types = ['Все'] + sorted(list(set(r['type'] for r in self.full_rows)))

            with ui.row().classes('w-full gap-2 items-center mb-4 wrap'):
                search_input = ui.input(
                    placeholder='Поиск пары / текста...',
                    on_change=apply_filters
                ).classes('w-48').props('dense outlined').bind_value(state, 'search_text')

                ex_select = ui.select(
                    initial_exchanges, 
                    label='Биржа', 
                    on_change=apply_filters
                ).classes('w-32').props('dense outlined').bind_value(state, 'filter_exchange')

                type_select = ui.select(
                    initial_types, 
                    label='Тип алерта', 
                    on_change=apply_filters
                ).classes('w-40').props('dense outlined').bind_value(state, 'filter_type')
                
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

            # Кастомизация столбца Действия (Заглушить и Удалить)
            self.table.add_slot('body-cell-actions', '''
                <q-td :props="props" class="flex flex-nowrap gap-1 justify-center items-center h-full pt-3">
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
            
            # Обработка события нажатия из JS
            self.table.on('toggle_mute', lambda msg: handle_toggle_mute(msg.args))
            self.table.on('delete_signal', lambda msg: handle_delete_signal(msg.args))

@ui.page('/signals')
async def signals_page():
    create_header()
    page = SignalsPage()
    await page.render()
