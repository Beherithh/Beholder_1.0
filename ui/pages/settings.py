import tkinter as tk
from tkinter import filedialog
import asyncio
import json
from nicegui import ui
from loguru import logger
from sqlmodel import select, delete

from database.core import get_session
from database.models import AppSettings, MonitoredPair, MarketData, DelistingEvent, Signal
from services.file_watcher import FileWatcherService
from ui.layout import create_header

class SettingsPage:
    def __init__(self):
        # Структура: [{"path": "...", "name": "..."}, ...]
        self.files_list = []
        self.stats_label = None
        
    async def load_settings(self):
        """Загружаем список файлов из БД"""
        async with get_session() as session:
            settings = await session.get(AppSettings, "watched_files")
            if settings:
                data = json.loads(settings.value)
                # Migration: Если в базе старый формат (список строк), конвертируем в словари
                if data and isinstance(data[0], str):
                    self.files_list = [{"path": p, "name": f"List {i+1}"} for i, p in enumerate(data)]
                else:
                    self.files_list = data
            else:
                self.files_list = []
                
    async def save_settings(self):
        """Сохраняем список файлов в БД"""
        async with get_session() as session:
            settings = await session.get(AppSettings, "watched_files")
            if not settings:
                settings = AppSettings(key="watched_files", value="[]")
                session.add(settings)
            
            settings.value = json.dumps(self.files_list)
            await session.commit()
            
    async def add_file(self, path_input):
        path = path_input.value
        if not path:
            return

        # Проверка на дубликаты пути
        for f in self.files_list:
            if f["path"] == path:
                ui.notify(f'Файл уже добавлен: {path}', type='warning')
                return

        # Запрашиваем уникальное имя
        with ui.dialog() as dialog, ui.card():
            ui.label('Введите уникальное имя для списка:')
            name_input = ui.input('Название').classes('w-full').props('autofocus')
            with ui.row():
                ui.button('OK', on_click=lambda: dialog.submit(name_input.value)).classes('bg-blue-600 text-white')
                ui.button('Cancel', on_click=lambda: dialog.submit(None))
        
        chosen_name = await dialog
        if not chosen_name:
            return # Cancelled

        self.files_list.append({"path": path, "name": chosen_name})
        await self.save_settings()
        path_input.value = ""
        self.refresh_ui()
        ui.notify(f'Список добавлен: {chosen_name}, {path}', type='positive')

    async def remove_file(self, item):
        if item in self.files_list:
            self.files_list.remove(item)
            await self.save_settings()
            self.refresh_ui()
            ui.notify(f'Удалено: {item["name"]}', type='warning')

    async def edit_name(self, item):
        with ui.dialog() as dialog, ui.card():
            ui.label(f'Изменить имя для {item["name"]}:')
            name_input = ui.input(value=item["name"]).classes('w-full').props('autofocus')
            with ui.row():
                ui.button('Save', on_click=lambda: dialog.submit(name_input.value)).classes('bg-blue-600')
                ui.button('Cancel', on_click=lambda: dialog.submit(None))
        
        new_name = await dialog
        if new_name and new_name != item["name"]:
            item["name"] = new_name
            await self.save_settings()
            self.refresh_ui()
    
    def refresh_ui(self):
        self.files_container.clear()
        with self.files_container:
            if not self.files_list:
                ui.label("Список файлов пуст").classes('text-gray-400 italic')
            
            for item in self.files_list:
                with ui.row().classes('items-center w-full justify-between bg-gray-100 p-2 rounded gap-2'):
                    # Левая часть: Имя и Путь
                    with ui.column().classes('gap-0'):
                        with ui.row().classes('items-center gap-2'):
                            ui.label(item["name"]).classes('font-bold text-blue-900')
                            ui.icon('edit', size='xs').classes('cursor-pointer text-gray-400 hover:text-blue-500').on('click', lambda i=item: self.edit_name(i))
                        ui.label(item["path"]).classes('text-xs text-gray-500 font-mono')
                    
                    # Правая часть: Кнопка удаления
                    ui.button(icon='delete', color='red', on_click=lambda i=item: self.remove_file(i)).props('flat dense')

    async def pick_file(self, target_input):
        def _open_dialog():
            root = tk.Tk()
            root.withdraw() 
            root.wm_attributes('-topmost', 1) 
            file_path = filedialog.askopenfilename(
                title="Выберите файл с парами (JSON)",
                filetypes=[("All files", "*.*"), ("Text/JSON files", "*.txt *.json")]
            )
            root.destroy()
            return file_path
        
        path = await asyncio.get_running_loop().run_in_executor(None, _open_dialog)
        if path:
            target_input.value = path
            
    async def run_sync(self):
        """Ручной запуск синхронизации"""
        ui.notify('Запуск синхронизации...')
        # Приводим self.files_list к формату, который ждет сервис (он уже обновлен на List[Dict]?)
        # Да, сервис ждет List[Dict], и self.files_list уже List[Dict].
        watcher = FileWatcherService(get_session)
        stats = await watcher.sync_files(self.files_list)
        ui.notify(f'Готово! {stats}', type='positive')
        if self.stats_label:
            self.stats_label.text = str(stats)

    async def _clear_table(self, model, name_ru):
        """Generic method to clear a table"""
        async with get_session() as session:
            # Считаем количество для отчета
            result = await session.execute(select(model))
            count_val = len(result.all())
                
            await session.execute(delete(model))
            await session.commit()
        ui.notify(f'{name_ru}: очистка выполнена, удалено {count_val} записей', type='positive')

    async def _show_confirm_dialog(self, text: str) -> bool:
        """Показывает диалог подтверждения"""
        with ui.dialog() as dialog, ui.card():
            ui.label(text)
            with ui.row():
                ui.button('Да, удалить', on_click=lambda: dialog.submit(True)).classes('bg-red-600 text-white')
                ui.button('Отмена', on_click=lambda: dialog.submit(False))
        return await dialog

    async def clear_market_data(self):
        if await self._show_confirm_dialog('Вы уверены? Это удалит ВСЕ исторические свечи (MarketData).'):
            await self._clear_table(MarketData, "MarketData")

    async def clear_monitored_pairs(self):
        if await self._show_confirm_dialog('Вы уверены? Это удалит список отслеживаемых пар (MonitoredPair).'):
            await self._clear_table(MonitoredPair, "MonitoredPair")

    async def clear_signals(self):
        if await self._show_confirm_dialog('Вы уверены? Это удалит все сигналы и события делистинга.'):
            await self._clear_table(Signal, "Signal (Сигналы)")
            await self._clear_table(DelistingEvent, "DelistingEvent (События)")

    async def render(self):
        await self.load_settings()
        
        # Получаем планировщик (он уже инициализирован в main)
        from services.system import get_scheduler
        scheduler = get_scheduler()
        
        with ui.card().classes('w-full max-w-3xl mx-auto p-4'):
            ui.label('Списки торгуемых пар').classes('text-xl font-bold mb-4')
            
            # Input Area
            with ui.row().classes('w-full items-center gap-2'):
                path_input = ui.input('Путь к файлу').classes('flex-grow').props('outlined dense')
                # Кнопка выбора файла (native dialog)
                ui.button(icon='folder', on_click=lambda: self.pick_file(path_input)).props('flat dense').tooltip('Выбрать файл на диске')
                ui.button('Добавить', on_click=lambda: self.add_file(path_input)).classes('bg-blue-500 text-white')
            
            # List Area
            ui.separator().classes('my-4')
            self.files_container = ui.column().classes('w-full gap-2')
            self.refresh_ui()
            
            # --- Scheduler Settings ---
            ui.separator().classes('my-4')
            ui.label('Расписание').classes('text-lg font-bold')
            
            # Генератор опций для селекта: {1: '1 час', 2: '2 часа', ...}
            # Можно сделать красивее с окончаниями, но для простоты: "X ч."
            hours_options = {h: f'{h} ч.' for h in range(1, 25)}

            # --- OHLCV Interval ---
            ui.label('Обновление OHLCv').classes('text-md font-medium mt-2')
            async def on_market_change(e):
                val = int(e.value)
                await scheduler.update_interval(val)
                ui.notify(f'Interval OHLCv: {val} ч.', type='positive')

            current_market_interval = 1
            async with get_session() as session:
                s = await session.get(AppSettings, "update_interval_hours")
                if s: current_market_interval = int(s.value)

            ui.select(options=hours_options, value=current_market_interval, on_change=on_market_change).classes('w-32')

            # --- Scraper Interval ---
            ui.label('Проверка делистингов (Scraper)').classes('text-md font-medium mt-2')
            async def on_scraper_change(e):
                val = e.value
                await scheduler.update_scraper_interval(val)
                ui.notify(f'Interval Scraper: {val} ч.', type='positive')
                
            current_scraper_interval = 1
            async with get_session() as session:
                s = await session.get(AppSettings, "scraper_interval_hours")
                if s: current_scraper_interval = int(s.value)

            ui.select(options=hours_options, value=current_scraper_interval, on_change=on_scraper_change).classes('w-32')

            # --- Action Area ---
            ui.separator().classes('my-4')
            ui.label('Ручное управление').classes('text-lg font-bold')
            with ui.row().classes('gap-2'):
                ui.button('Синхронизация отслеживаемых пар', on_click=self.run_sync).classes('bg-green-600')
                ui.button('Обновить OHLCv', on_click=lambda: scheduler.market_service.update_all()).classes('bg-orange-600')
                ui.button('Проверить делистинги (Scraper)', on_click=lambda: scheduler.scraper_service.check_delistings_blog()).classes('bg-red-600')
            
            self.stats_label = ui.label('').classes('text-sm text-gray-500 mt-2')

            # --- Database Management ---
            ui.separator().classes('my-4')
            ui.label('Управление Базой Данных').classes('text-lg font-bold text-red-800')
            
            with ui.row().classes('gap-2'):
                ui.button('Очистить OHLCv (MarketData)', on_click=self.clear_market_data).props('outline color=red')
                ui.button('Очистить отслеживаемые пары', on_click=self.clear_monitored_pairs).props('outline color=red')
                ui.button('Очистить БД делистингов и сигналов', on_click=self.clear_signals).props('outline color=red')

@ui.page('/settings')
async def settings_page():
    # Header

    create_header()
        
    # Main Content
    page = SettingsPage()
    await page.render()
