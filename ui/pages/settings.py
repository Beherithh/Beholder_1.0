import tkinter as tk
from tkinter import filedialog
import asyncio
import json
from nicegui import ui
from loguru import logger
from sqlmodel import select

from database.core import get_session
from database.models import AppSettings, MonitoredPair
from services.file_watcher import FileWatcherService

class SettingsPage:
    def __init__(self):
        self.files_list = []
        self.stats_label = None
        
    async def load_settings(self):
        """Загружаем список файлов из БД"""
        async with get_session() as session:
            settings = await session.get(AppSettings, "watched_files")
            if settings:
                self.files_list = json.loads(settings.value)
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
        if path and path not in self.files_list:
            self.files_list.append(path)
            await self.save_settings()
            path_input.value = ""
            self.refresh_ui()
            ui.notify(f'Файл добавлен: {path}', type='positive')

    async def remove_file(self, path):
        if path in self.files_list:
            self.files_list.remove(path)
            await self.save_settings()
            self.refresh_ui()
            ui.notify(f'Файл удален: {path}', type='warning')
            
    async def run_sync(self):
        """Ручной запуск синхронизации"""
        ui.notify('Запуск синхронизации...')
        watcher = FileWatcherService(get_session)
        stats = await watcher.sync_files(self.files_list)
        ui.notify(f'Готово! {stats}', type='positive')
        if self.stats_label:
            self.stats_label.text = str(stats)

    def refresh_ui(self):
        self.files_container.clear()
        with self.files_container:
            if not self.files_list:
                ui.label("Список файлов пуст").classes('text-gray-400 italic')
            
            for path in self.files_list:
                with ui.row().classes('items-center w-full justify-between bg-gray-100 p-2 rounded'):
                    ui.label(path).classes('text-sm font-mono')
                    ui.button(icon='delete', color='red', on_click=lambda p=path: self.remove_file(p)).props('flat dense')

    async def pick_file(self, target_input):
        def _open_dialog():
            # Создаем скрытое окно root
            root = tk.Tk()
            root.withdraw() 
            root.wm_attributes('-topmost', 1) # Поверх всех окон
            
            file_path = filedialog.askopenfilename(
                title="Выберите файл с парами (JSON)",
                filetypes=[("All files", "*.*"), ("Text/JSON files", "*.txt *.json")]
            )
            
            root.destroy()
            return file_path
        
        # Запускаем диалог в отдельном потоке, чтобы не блокировать веб-сервер
        path = await asyncio.get_running_loop().run_in_executor(None, _open_dialog)
        
        if path:
            target_input.value = path

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
            ui.label('Расписание обновлений истории OHLCv').classes('text-lg font-bold')
            
            # Слайдер интервала
            # Получаем текущий интервал из настроек scheduler'а (можно было бы хранить в memory, но лучше читать из БД или scheduler job)
            # Для простоты пока дефолт 1 или то, что в slider.value
            
            async def on_slider_change(e):
                val = int(e.value)
                label_interval.text = f'Каждые {val} ч.'
                await scheduler.update_interval(val)
                ui.notify(f'Интервал изменен: {val} ч.', type='positive')

            # Ищем текущее значение в БД для инициализации слайдера
            current_interval = 1
            async with get_session() as session:
                s = await session.get(AppSettings, "update_interval_hours")
                if s: 
                    current_interval = int(s.value)

            with ui.row().classes('w-full items-center gap-4'):
                slider = ui.slider(min=1, max=24, value=current_interval, step=1, on_change=on_slider_change).props('label-always')
                label_interval = ui.label(f'Каждые {current_interval} ч.').classes('min-w-[100px]')

            # --- Action Area ---
            ui.separator().classes('my-4')
            ui.label('Ручное управление').classes('text-lg font-bold')
            with ui.row().classes('gap-2'):
                ui.button('Синхронизация торгуемых пар', on_click=self.run_sync).classes('bg-green-600')
                ui.button('Обновить свечи', on_click=lambda: scheduler.market_service.update_all()).classes('bg-orange-600')
                ui.button('Проверить делистинги (Scraper)', on_click=lambda: scheduler.scraper_service.check_delistings_blog()).classes('bg-red-600')
            
            self.stats_label = ui.label('').classes('text-sm text-gray-500 mt-2')

@ui.page('/settings')
async def settings_page():
    # Header
    with ui.header().classes('bg-blue-800 text-white p-4'):
        ui.label('Beholder / Настройки').classes('text-lg font-bold')
        
    # Main Content
    page = SettingsPage()
    await page.render()
