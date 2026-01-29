from nicegui import ui
from sqlmodel import select, desc, delete
from database.core import get_session
from database.models import Signal, SignalType
from ui.layout import create_header
from loguru import logger

class SignalsPage:
    def __init__(self):
        self.signals = []
        self.table = None

    async def load_signals(self):
        """Загрузка последних 100 сигналов из БД"""
        async with get_session() as session:
            # SQLModel/SQLAlchemy selection for top 100 signals ordered by date
            statement = select(Signal).order_by(desc(Signal.created_at)).limit(100)
            result = await session.execute(statement)
            self.signals = result.scalars().all()

    async def delete_signal(self, signal_id: int):
        """Удаление одного сигнала"""
        async with get_session() as session:
            await session.execute(delete(Signal).where(Signal.id == signal_id))
            await session.commit()
        ui.notify(f'Сигнал #{signal_id} удален', type='info')
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
        if self.table:
            # Преобразуем данные для таблицы NiceGUI
            rows = []
            for s in self.signals:
                rows.append({
                    'id': s.id,
                    'time': s.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                    'type': s.type.value,
                    'message': s.raw_message,
                    'sent': s.is_sent,
                    'type_raw': s.type # Для кастомного рендеринга
                })
            self.table.rows[:] = rows
            self.table.update()

    async def render(self):
        await self.load_signals()
        
        with ui.card().classes('w-full max-w-6xl mx-auto p-4'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('История сигналов (последние 100)').classes('text-2xl font-bold')
                ui.button(icon='refresh', on_click=self.refresh_table).props('flat dense')

            columns = [
                {'name': 'time', 'label': 'Время', 'field': 'time', 'sortable': True, 'align': 'left'},
                {'name': 'type', 'label': 'Тип', 'field': 'type', 'sortable': True, 'align': 'center'},
                {'name': 'sent', 'label': 'Отправлен', 'field': 'sent', 'sortable': True, 'align': 'center'},
                {'name': 'message', 'label': 'Сообщение', 'field': 'message', 'align': 'left', 'classes': 'whitespace-pre-line'},
                {'name': 'actions', 'label': 'Действия', 'field': 'id', 'align': 'center'},
            ]

            # Создаем начальные строки
            initial_rows = []
            for s in self.signals:
                initial_rows.append({
                    'id': s.id,
                    'time': s.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                    'type': s.type.value,
                    'message': s.raw_message,
                    'sent': s.is_sent,
                    'type_raw': s.type
                })

            self.table = ui.table(columns=columns, rows=initial_rows, row_key='id').classes('w-full')
            
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

            # Кастомизация столбца Действия
            self.table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat round dense icon="delete" color="red" @click="$parent.$emit('delete', props.row.id)" />
                </q-td>
            ''')
            
            # Обработка события нажатия из JS
            self.table.on('delete', lambda msg: self.delete_signal(msg.args))

@ui.page('/signals')
async def signals_page():
    create_header()
    page = SignalsPage()
    await page.render()
