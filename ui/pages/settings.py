import tkinter as tk
from tkinter import filedialog
import asyncio
import json
from nicegui import ui
from loguru import logger
import os

from database.core import get_session
from database.models import AppSettings
from services.file_watcher import FileWatcherService
from services.system import get_scraper_service, get_config_service, get_scheduler
from ui.layout import create_header

class SettingsPage:
    def __init__(self):
        # Структура: [{"path": "...", "name": "..."}, ...]
        self.files_list = []
        
        # Настройки Telegram
        self.tg_token = ""
        self.tg_chat_id = ""
        self.tg_api_id = ""
        self.tg_api_hash = ""
        
        # Настройки алертов
        self.alert_price_hours_pump_period = None
        self.alert_price_hours_dump_period = None
        self.alert_price_hours_pump_threshold = None
        self.alert_price_hours_dump_threshold = None
        self.alert_price_days_pump_period = None
        self.alert_price_days_dump_period = None
        self.alert_price_days_pump_threshold = None
        self.alert_price_days_dump_threshold = None
        self.alert_volume_days_period = None
        self.alert_volume_days_threshold = None
        self.alert_dedup_hours = 12
        
        # Настройки CoinMarketCap
        self.cmc_api_key = ""
        self.cmc_rank_threshold = 500
        
        # Настройки планировщика
        self.market_interval = 1
        self.scraper_interval = 1
        self.cmc_interval = 5
        
        # UI контейнеры (инициализируются в render)
        self.files_container = None
        
    async def load_settings(self):
        """Загружаем все настройки через ConfigService"""
        config = get_config_service()
        
        # Файлы
        self.files_list = await config.get_watched_files()
        
        # Telegram
        tg_conf = await config.get_telegram_config()
        self.tg_token = tg_conf.bot_token
        self.tg_chat_id = tg_conf.chat_id
        self.tg_api_id = tg_conf.api_id
        self.tg_api_hash = tg_conf.api_hash
        
        # Алерты
        alert_conf = await config.get_alert_config()
        self.alert_price_hours_pump_period = alert_conf.h_pump_period
        self.alert_price_hours_dump_period = alert_conf.h_dump_period
        self.alert_price_hours_pump_threshold = alert_conf.h_pump_threshold
        self.alert_price_hours_dump_threshold = alert_conf.h_dump_threshold
        self.alert_price_days_pump_period = alert_conf.d_pump_period
        self.alert_price_days_dump_period = alert_conf.d_dump_period
        self.alert_price_days_pump_threshold = alert_conf.d_pump_threshold
        self.alert_price_days_dump_threshold = alert_conf.d_dump_threshold
        self.alert_volume_days_period = alert_conf.v_period
        self.alert_volume_days_threshold = alert_conf.v_threshold
        self.alert_dedup_hours = alert_conf.dedup_hours
        
        # CMC
        cmc_conf = await config.get_cmc_config()
        self.cmc_api_key = cmc_conf.api_key
        self.cmc_rank_threshold = cmc_conf.rank_threshold
        
        # Scheduler
        scheduler_conf = await config.get_scheduler_config()
        self.market_interval = scheduler_conf.market_update_interval_hours
        self.scraper_interval = scheduler_conf.scraper_interval_hours
        self.cmc_interval = scheduler_conf.cmc_update_interval_days
                
    async def save_settings(self):
        """Универсальный метод сохранения всех настроек со страницы."""
        async with get_session() as session:
            
            async def set_val(key, value):
                obj = await session.get(AppSettings, key)
                if not obj:
                    session.add(AppSettings(key=key, value=str(value)))
                else:
                    obj.value = str(value)

            # Сохраняем список файлов в БД
            await set_val("watched_files", json.dumps(self.files_list))
            
            # Сохраняем настройки Telegram
            await set_val("tg_bot_token", self.tg_token)
            await set_val("tg_chat_id", self.tg_chat_id)
            await set_val("tg_api_id", self.tg_api_id)
            await set_val("tg_api_hash", self.tg_api_hash)

            # Сохраняем алерты
            await set_val("alert_price_hours_pump_period", self.alert_price_hours_pump_period)
            await set_val("alert_price_hours_dump_period", self.alert_price_hours_dump_period)
            await set_val("alert_price_hours_pump_threshold", self.alert_price_hours_pump_threshold)
            await set_val("alert_price_hours_dump_threshold", self.alert_price_hours_dump_threshold)
            await set_val("alert_price_days_pump_period", self.alert_price_days_pump_period)
            await set_val("alert_price_days_dump_period", self.alert_price_days_dump_period)
            await set_val("alert_price_days_pump_threshold", self.alert_price_days_pump_threshold)
            await set_val("alert_price_days_dump_threshold", self.alert_price_days_dump_threshold)
            await set_val("alert_volume_days_period", self.alert_volume_days_period)
            await set_val("alert_volume_days_threshold", self.alert_volume_days_threshold)
            await set_val("alert_dedup_hours", self.alert_dedup_hours)
            
            # Сохраняем настройки CMC
            await set_val("cmc_api_key", self.cmc_api_key)
            await set_val("cmc_rank_threshold", self.cmc_rank_threshold)
            
            # Сохраняем настройки планировщика
            await set_val("update_interval_hours", self.market_interval)
            await set_val("scraper_interval_hours", self.scraper_interval)
            await set_val("cmc_update_interval_days", self.cmc_interval)

            await session.commit()
            
        # Обновляем сервис Telegram
        from services.system import get_telegram_service
        get_telegram_service().update_config(self.tg_token, self.tg_chat_id)
        ui.notify("Настройки сохранены", type="positive")
            
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
        ui.notify(f'Список добавлен: {chosen_name}, {path}', type='positive')
        
        # Автоматическая синхронизация сразу после добавления
        try:
            watcher = FileWatcherService(get_session)
            stats = await watcher.sync_from_settings()
            
            # Быстрый матч с рисками
            async with get_session() as session:
                scraper = get_scraper_service()
                matches = await scraper.match_monitored_pairs_with_events(session)
            
            # Проверяем наличие ошибок (отсутствующие файлы)
            missing = stats.get("missing_files", [])
            if missing:
                ui.notify(f'Внимание! Не найдены файлы: {", ".join(missing)}', type='negative', timeout=10000)
            
            stats_str = f"Added: {stats.get('added', 0)}, Reactivated: {stats.get('reactivated', 0)}, Archived: {stats.get('archived', 0)}"
            ui.notify(f'Авто-синхронизация: {stats_str}. Найдено совпадений: {matches}', type='positive')
        except Exception as e:
            logger.error(f"Ошибка авто-синхронизации: {e}")
            ui.notify('Ошибка при автоматической синхронизации', type='negative')

        self.refresh_ui()

    async def remove_file(self, item):
        if item in self.files_list:
            self.files_list.remove(item)
            await self.save_settings()
            ui.notify(f'Удалено: {item["name"]}', type='warning')
            
            # Автоматическая синхронизация сразу после удаления
            try:
                watcher = FileWatcherService(get_session)
                stats = await watcher.sync_from_settings()
                
                # Проверяем наличие ошибок (отсутствующие файлы)
                missing = stats.get("missing_files", [])
                if missing:
                    ui.notify(f'Внимание! Не найдены файлы: {", ".join(missing)}', type='negative', timeout=10000)
                
                stats_str = f"Added: {stats.get('added', 0)}, Reactivated: {stats.get('reactivated', 0)}, Archived: {stats.get('archived', 0)}"
                ui.notify(f'Список обновлен: {stats_str}', type='positive')
            except Exception as e:
                logger.error(f"Ошибка авто-синхронизации после удаления: {e}")

            self.refresh_ui()

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
            ui.notify(f'Имя обновлено: {new_name}', type='positive')
            self.refresh_ui()
    
    def refresh_ui(self):
        if self.files_container is None:
            return
            
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

    @staticmethod
    async def pick_file(target_input):
        """Отрисовывает интерфейс выбора файла из файловой системы сервера."""
        # Начинаем с текущей рабочей директории сервера (или укажите абсолютный путь, например 'C:\\')
        start_path = os.path.abspath('.')

        with ui.dialog() as dialog, ui.card().classes('w-[500px] h-[600px] flex flex-col'):
            ui.label('Выберите файл на сервере').classes('text-lg font-bold mb-2')

            # Поле для отображения текущего пути на сервере
            current_path_label = ui.label(start_path).classes('text-xs text-gray-500 font-mono mb-2 break-all')

            # Контейнер для списка папок и файлов
            file_list_container = ui.column().classes('overflow-y-auto flex-grow w-full border rounded p-2')

            def update_list(path):
                current_path_label.set_text(path)
                file_list_container.clear()

                with file_list_container:
                    # Кнопка возврата на уровень выше (если не в корне)
                    parent = os.path.dirname(path)
                    if parent != path:
                        ui.button('📁 [Вверх]', on_click=lambda p=parent: update_list(p)).props(
                            'flat dense align=left w-full').classes('text-blue-600')

                    try:
                        items = os.listdir(path)
                    except PermissionError:
                        ui.label('Отказано в доступе к директории').classes('text-red-500 mt-2')
                        return
                    except FileNotFoundError:
                        ui.label('Директория не найдена').classes('text-red-500 mt-2')
                        return

                    # Разделяем на папки и файлы для удобной сортировки
                    dirs = sorted([d for d in items if os.path.isdir(os.path.join(path, d))])
                    files = sorted([f for f in items if os.path.isfile(os.path.join(path, f))])

                    # Отрисовка папок
                    for d in dirs:
                        full_dir = os.path.join(path, d)
                        ui.button(f'📁 {d}', on_click=lambda p=full_dir: update_list(p)).props(
                            'flat dense align=left w-full').classes('text-blue-500 lowercase')

                    # Отрисовка всех файлов (фильтрация убрана)
                    for f in files:
                        full_file = os.path.join(path, f)
                        ui.button(f'📄 {f}', on_click=lambda p=full_file: dialog.submit(p)).props(
                                 'flat dense align=left w-full').classes('text-gray-700')

            # Инициализируем список для стартовой директории
            update_list(start_path)

            # Кнопка отмены
            with ui.row().classes('w-full justify-end mt-4'):
                ui.button('Отмена', on_click=lambda: dialog.submit(None)).classes('bg-gray-400 text-white')

        # Ожидаем выбор пользователя
        result = await dialog
        if result:
            target_input.value = result


    async def test_telegram(self):
        """Проверка связи с ТГ"""
        from services.system import get_telegram_service
        # Временно обновляем конфиг из полей ввода перед тестом
        tg = get_telegram_service()
        tg.update_config(self.tg_token, self.tg_chat_id)
        
        ui.notify('Отправка тестового сообщения...', type='info')
        success = await tg.test_connection()
        if success:
            ui.notify('Тест пройден! Проверьте Telegram.', type='positive')
            await self.save_settings() # Если тест ок, сразу сохраняем
        else:
            ui.notify('Ошибка теста Telegram. Проверьте Token и Chat ID.', type='negative')

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
            
            # --- Telegram Settings ---
            ui.separator().classes('my-4')
            ui.label('Уведомления Telegram').classes('text-xl font-bold mb-2')
            with ui.row().classes('w-full items-center gap-4'):
                token_input = ui.input('Bot Token', password=True, password_toggle_button=True).classes('flex-grow').bind_value(self, 'tg_token')
                chat_input = ui.input('Chat ID').classes('w-32').bind_value(self, 'tg_chat_id')
                ui.button('Тест', on_click=self.test_telegram).props('outline').classes('h-10')
                ui.button('Сохранить', on_click=self.save_settings).classes('bg-green-600 text-white h-10')
            ui.label('Создайте бота через @BotFather и получите свой ID через @userinfobot').classes('text-xs text-gray-400')
            
            # --- Telegram API (Pyrogram) ---
            ui.separator().classes('my-2')
            ui.label('Telegram API (для чтения каналов)').classes('text-lg font-bold mb-2')
            with ui.row().classes('w-full items-center gap-4'):
                ui.input('API ID', placeholder='12345678').classes('w-40').bind_value(self, 'tg_api_id')
                ui.input('API Hash', password=True, password_toggle_button=True, placeholder='0123456789abcdef...').classes('flex-grow').bind_value(self, 'tg_api_hash')
                ui.button('Сохранить', on_click=self.save_settings).classes('bg-green-600 text-white h-10')
            ui.label('Получите credentials на my.telegram.org → API development tools. Используется для парсинга @BinanceAnnouncements').classes('text-xs text-gray-400')


            # --- CoinMarketCap Settings ---
            ui.separator().classes('my-4')
            with ui.row().classes('items-center gap-2'):
                ui.label('CoinMarketCap API (порог хлама по умолчаню = 500)').classes('text-xl font-bold')
            with ui.row().classes('w-full items-center gap-4'):
                ui.input('API Key', password=True, password_toggle_button=True).classes('flex-grow').bind_value(self, 'cmc_api_key')
                ui.number('Порог рейтинга хлама', min=1).classes('w-32').bind_value(self, 'cmc_rank_threshold').props('dense')
                ui.button('Сохранить', on_click=self.save_settings).classes('bg-green-600 text-white h-10')

            # --- Scheduler Settings ---
            ui.separator().classes('my-4')
            ui.label('Расписание').classes('text-lg font-bold')
            
            async def on_interval_change(setting_attr: str, new_value: int, reschedule_func):
                # Обновляем внутреннее состояние
                setattr(self, setting_attr, new_value)
                # Сохраняем все настройки (включая эту)
                await self.save_settings()
                # Перепланируем конкретную задачу
                await reschedule_func(new_value)
                ui.notify(f'Интервал {setting_attr} обновлен: {new_value}', type='positive')

            with ui.grid(columns=3):
                # Генератор опций для селекта: {1: '1 час', 2: '2 часа', ...}
                # Можно сделать красивее с окончаниями, но для простоты: "X ч."
                hours_options = {h: f'{h} ч.' for h in range(1, 25)}
                # --- OHLCV Interval ---
                ui.label('Обновление OHLCv').classes('text-md font-medium mt-2')
                
                # --- Scraper Interval ---
                ui.label('Проверка делистингов (Scraper)').classes('text-md font-medium mt-2')
                
                # --- CMC Interval ---
                ui.label('Обновление рангов CMC').classes('text-md font-medium mt-2')
                
                # Опции для дней: 1...30
                days_options = {d: f'{d} дн.' for d in range(1, 31)}

                ui.select(options=hours_options, value=self.market_interval, on_change=lambda e: on_interval_change('market_interval', e.value, scheduler.update_market_interval)).classes('w-32').bind_value(self, 'market_interval')
                ui.select(options=hours_options, value=self.scraper_interval, on_change=lambda e: on_interval_change('scraper_interval', e.value, scheduler.update_scraper_interval)).classes('w-32').bind_value(self, 'scraper_interval')
                ui.select(options=days_options, value=self.cmc_interval, on_change=lambda e: on_interval_change('cmc_interval', e.value, scheduler.update_cmc_interval)).classes('w-32').bind_value(self, 'cmc_interval')

            # Пояснение логики работы всех служб
            with ui.column().classes('w-full gap-2 p-3 bg-blue-50 rounded border-l-4 border-blue-400 mt-2'):
                ui.label('ℹ️ Как работают службы мониторинга:').classes('text-sm font-bold text-blue-700')
                ui.markdown('''
**Расписание запуска:**
- **OHLCV (свечи)** — каждый час в **:05** (10:05, 11:05, 12:05...)
- **Scraper (делистинги и ST)** — каждый час в **:15** (10:15, 11:15, 12:15...)

**Что происходит при запуске OHLCV** (:05):
1. Загрузка свечей для всех активных пар
2. Анализ изменения цены (Pump/Dump)
3. Анализ объёма торгов (USDT)
4. Отправка алертов в Telegram (если пороги превышены)

**Что происходит при запуске Scraper** (:15):
1. **Auto-Sync:** автоматическое обновление списка пар из файлов
2. **Матчинг:** сверка новых пар с историей делистингов/ST
3. **Blog Scraping:** поиск новых статей на сайтах
4. **API Check:** проверка ST-тегов через API бирж
5. Отправка уведомлений о новых рисках

**Интервалы > 1 часа (примеры):**
- **6 часов** → OHLCV: 00:05, 06:05, 12:05, 18:05 | Scraper: 00:15, 06:15, 12:15, 18:15
- **14 часов** → Scraper: 00:15, 14:15 (2 раза в сутки)
- **24 часа** → OHLCV: 00:05 (раз в сутки)

*⚠️ Это фиксированное время (модуль часа), а не "каждые X часов от старта".*
                ''').classes('text-xs text-gray-700')

            # --- Analysis Alerts Settings ---
            ui.separator().classes('my-4')
            ui.label('Настройки алертов (Цена и Объем)').classes('text-xl font-bold mb-2')
            
            with ui.column().classes('w-full gap-4'):
                # Pump Alerts
                ui.label('📈 PUMP (рост цены)').classes('text-lg font-bold text-green-600')
                with ui.row().classes('items-center gap-4 ml-4'):
                    ui.label('Часы:').classes('w-32')
                    ui.number('Период (ч)', min=1, max=168).classes('w-24').bind_value(self, 'alert_price_hours_pump_period').props('dense')
                    ui.number('Порог %', min=0.1).classes('w-24').bind_value(self, 'alert_price_hours_pump_threshold').props('dense suffix=%')
                
                with ui.row().classes('items-center gap-4 ml-4'):
                    ui.label('Дни:').classes('w-32')
                    ui.number('Период (дн)', min=1, max=30).classes('w-24').bind_value(self, 'alert_price_days_pump_period').props('dense')
                    ui.number('Порог %', min=0.1).classes('w-24').bind_value(self, 'alert_price_days_pump_threshold').props('dense suffix=%')
                
                ui.separator().classes('my-2')
                
                # Dump Alerts
                ui.label('📉 DUMP (падение цены)').classes('text-lg font-bold text-red-600')
                with ui.row().classes('items-center gap-4 ml-4'):
                    ui.label('Часы:').classes('w-32')
                    ui.number('Период (ч)', min=1, max=168).classes('w-24').bind_value(self, 'alert_price_hours_dump_period').props('dense')
                    ui.number('Порог %', min=0.1).classes('w-24').bind_value(self, 'alert_price_hours_dump_threshold').props('dense suffix=%')
                
                with ui.row().classes('items-center gap-4 ml-4'):
                    ui.label('Дни:').classes('w-32')
                    ui.number('Период (дн)', min=1, max=30).classes('w-24').bind_value(self, 'alert_price_days_dump_period').props('dense')
                    ui.number('Порог %', min=0.1).classes('w-24').bind_value(self, 'alert_price_days_dump_threshold').props('dense suffix=%')
            
                ui.separator().classes('my-4')
                
                # Volume Days
                with ui.row().classes('items-center gap-4'):
                    ui.label('Объем торгов (дни):').classes('w-48')
                    ui.number('Дни', min=1, max=30).classes('w-24').bind_value(self, 'alert_volume_days_period').props('dense')
                    ui.number('Порог USDT в день', min=0).classes('w-40').bind_value(self, 'alert_volume_days_threshold').props('dense suffix=USDT')
                
                # Deduplication window
                ui.separator().classes('my-2')
                with ui.row().classes('items-center gap-4'):
                    ui.label('Блокировать одинаковые алерты:').classes('w-48')
                    ui.number('Часы', min=1, max=168).classes('w-24').bind_value(self, 'alert_dedup_hours').props('dense')
                    ui.label('(одинаковый алерт по той же паре не чаще чем раз в X часов)').classes('text-xs text-gray-400')
                
                ui.button('Сохранить настройки алертов', on_click=self.save_settings).classes('bg-blue-600 text-white w-fit self-end')
                ui.label('Если поля пустые - алерт считается выключенным. Пороги указываются как положительные числа.').classes('text-xs text-gray-400')


@ui.page('/settings')
async def settings_page():
    # Header

    create_header()
        
    # Main Content
    page = SettingsPage()
    await page.render()
