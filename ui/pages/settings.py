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
from services.system import get_scraper_service
from ui.layout import create_header

class SettingsPage:
    def __init__(self):
        # Структура: [{"path": "...", "name": "..."}, ...]
        self.files_list = []
        self.stats_label = None
        
        # Настройки Telegram
        self.tg_token = ""
        self.tg_chat_id = ""
        self.tg_api_id = ""
        self.tg_api_hash = ""
        
        # Состояния для кнопок управления
        self.is_syncing = False
        self.is_updating_ohlcv = False
        self.is_checking_news = False
        
        # Настройки алертов (по умолчанию пустые)
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
        self.alert_dedup_hours = 12  # блокировка повторных алертов (по умолчанию 12 часов)
        
        # Настройки CoinMarketCap
        self.cmc_api_key = ""
        self.cmc_rank_threshold = 500 # По умолчанию 500 порог хлама по СМС рангу
        self.cmc_update_interval_days = 5
        
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
            
            # Загружаем настройки Telegram
            token_set = await session.get(AppSettings, "tg_bot_token")
            self.tg_token = token_set.value if token_set else ""
            
            chat_id_set = await session.get(AppSettings, "tg_chat_id")
            self.tg_chat_id = chat_id_set.value if chat_id_set else ""
            
            api_id_set = await session.get(AppSettings, "tg_api_id")
            self.tg_api_id = api_id_set.value if api_id_set else ""
            
            api_hash_set = await session.get(AppSettings, "tg_api_hash")
            self.tg_api_hash = api_hash_set.value if api_hash_set else ""

            # Загружаем настройки алертов
            async def get_val_int(key):
                s = await session.get(AppSettings, key)
                try:
                    return int(float(s.value)) if s and s.value and s.value != 'None' else None
                except: return None

            async def get_val_float(key):
                s = await session.get(AppSettings, key)
                try:
                    return float(s.value) if s and s.value and s.value != 'None' else None
                except: return None

            # Fallback/Migration Logic:
            # If explicit pump/dump period is missing, try to load old "alert_price_hours_period"
            old_h_period = await get_val_int("alert_price_hours_period")
            old_d_period = await get_val_int("alert_price_days_period")

            self.alert_price_hours_pump_period = await get_val_int("alert_price_hours_pump_period") or old_h_period
            self.alert_price_hours_dump_period = await get_val_int("alert_price_hours_dump_period") or old_h_period
            
            self.alert_price_hours_pump_threshold = await get_val_float("alert_price_hours_pump_threshold")
            self.alert_price_hours_dump_threshold = await get_val_float("alert_price_hours_dump_threshold")
            
            self.alert_price_days_pump_period = await get_val_int("alert_price_days_pump_period") or old_d_period
            self.alert_price_days_dump_period = await get_val_int("alert_price_days_dump_period") or old_d_period
            
            self.alert_price_days_pump_threshold = await get_val_float("alert_price_days_pump_threshold")
            self.alert_price_days_dump_threshold = await get_val_float("alert_price_days_dump_threshold")
            self.alert_volume_days_period = await get_val_int("alert_volume_days_period")
            self.alert_volume_days_threshold = await get_val_float("alert_volume_days_threshold")
            
            dedup = await get_val_int("alert_dedup_hours")
            self.alert_dedup_hours = dedup if dedup else 12

            # Загружаем настройки CMC
            cmc_key_set = await session.get(AppSettings, "cmc_api_key")
            self.cmc_api_key = cmc_key_set.value if cmc_key_set else ""
            
            self.cmc_rank_threshold = await get_val_int("cmc_rank_threshold")
            
            cmc_interval = await get_val_int("cmc_update_interval_days")
            self.cmc_update_interval_days = cmc_interval if cmc_interval else 5
                
    async def save_settings(self):
        async with get_session() as session:
            # Сохраняем список файлов в БД
            settings = await session.get(AppSettings, "watched_files")
            if not settings:
                settings = AppSettings(key="watched_files", value="[]")
                session.add(settings)
            
            settings.value = json.dumps(self.files_list)
            
            # Сохраняем настройки Telegram
            token_set = await session.get(AppSettings, "tg_bot_token")
            if not token_set:
                token_set = AppSettings(key="tg_bot_token", value=self.tg_token)
                session.add(token_set)
            else:
                token_set.value = self.tg_token

            chat_id_set = await session.get(AppSettings, "tg_chat_id")
            if not chat_id_set:
                chat_id_set = AppSettings(key="tg_chat_id", value=self.tg_chat_id)
                session.add(chat_id_set)
            else:
                chat_id_set.value = self.tg_chat_id
            
            api_id_set = await session.get(AppSettings, "tg_api_id")
            if not api_id_set:
                api_id_set = AppSettings(key="tg_api_id", value=self.tg_api_id)
                session.add(api_id_set)
            else:
                api_id_set.value = self.tg_api_id
            
            api_hash_set = await session.get(AppSettings, "tg_api_hash")
            if not api_hash_set:
                api_hash_set = AppSettings(key="tg_api_hash", value=self.tg_api_hash)
                session.add(api_hash_set)
            else:
                api_hash_set.value = self.tg_api_hash

            # Сохраняем алерты
            alert_map = {
                "alert_price_hours_pump_period": str(self.alert_price_hours_pump_period),
                "alert_price_hours_dump_period": str(self.alert_price_hours_dump_period),
                "alert_price_hours_pump_threshold": str(self.alert_price_hours_pump_threshold),
                "alert_price_hours_dump_threshold": str(self.alert_price_hours_dump_threshold),
                "alert_price_days_pump_period": str(self.alert_price_days_pump_period),
                "alert_price_days_dump_period": str(self.alert_price_days_dump_period),
                "alert_price_days_pump_threshold": str(self.alert_price_days_pump_threshold),
                "alert_price_days_dump_threshold": str(self.alert_price_days_dump_threshold),
                "alert_volume_days_period": str(self.alert_volume_days_period),
                "alert_volume_days_threshold": str(self.alert_volume_days_threshold),
                "alert_dedup_hours": str(self.alert_dedup_hours),
            }
            for k, v in alert_map.items():
                obj = await session.get(AppSettings, k)
                if not obj:
                    session.add(AppSettings(key=k, value=v))
                else:
                    obj.value = v
            
            # Сохраняем настройки CMC
            cmc_key_set = await session.get(AppSettings, "cmc_api_key")
            if not cmc_key_set:
                session.add(AppSettings(key="cmc_api_key", value=self.cmc_api_key))
            else:
                cmc_key_set.value = self.cmc_api_key
                
            cmc_rank_threshold_set = await session.get(AppSettings, "cmc_rank_threshold")
            if not cmc_rank_threshold_set:
                session.add(AppSettings(key="cmc_rank_threshold", value=str(self.cmc_rank_threshold)))
            else:
                cmc_rank_threshold_set.value = str(self.cmc_rank_threshold)

            # Интервал CMC сохраняется через селект, но для порядка можно и тут (хотя лучше не дублировать логику с UI)
            # Селект вызывает update_cmc_interval сразу. 
            # Оставим тут только API Key и Threshold.

            await session.commit()
            
            # Обновляем сервис Telegram
            from services.system import get_telegram_service
            get_telegram_service().update_config(self.tg_token, self.tg_chat_id)
            
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
            
            ui.notify(f'Авто-синхронизация: {stats}. Найдено совпадений: {matches}', type='positive')
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
                ui.notify(f'Список обновлен: {stats}', type='positive')
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
            
    async def run_sync(self, button):
        """Ручной запуск синхронизации"""
        self.is_syncing = True
        ui.notify('Запуск синхронизации...', type='info')
        try:
            watcher = FileWatcherService(get_session)
            stats = await watcher.sync_files(self.files_list)
            
            # Запускаем БЫСТРЫЙ матч с историей рисков сразу после синхронизации
            async with get_session() as session:
                scraper = get_scraper_service()
                matches = await scraper.match_monitored_pairs_with_events(session)
                
            ui.notify(f'Синхронизация завершена! {stats}. Найдено совпадений: {matches}', type='positive')
            if self.stats_label:
                self.stats_label.text = f"{stats} | Matches: {matches}"
        finally:
            self.is_syncing = False

    async def run_ohlcv_update(self, scheduler):
        """Ручной запуск обновления цен"""
        self.is_updating_ohlcv = True
        ui.notify('Обновление цен запущено...', type='info')
        try:
            await scheduler.market_service.update_all()
            ui.notify('Цены обновлены!', type='positive')
        finally:
            self.is_updating_ohlcv = False

    async def run_scraper_check(self, scheduler):
        """Ручной запуск проверки новостей"""
        self.is_checking_news = True
        ui.notify('Запущена проверка новостей на Delist/ST...', type='info')
        try:
            await scheduler.scraper_service.check_all_risks()
            ui.notify('Проверка новостей завершена!', type='positive')
        finally:
            self.is_checking_news = False

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
        if await self._show_confirm_dialog('Вы уверены? Это удалит все сигналы (история уведомлений).'):
            await self._clear_table(Signal, "Signal (Сигналы)")

    async def clear_delistings(self):
        if await self._show_confirm_dialog('Вы уверены? Это удалит все найденные события делистинга/ST.'):
            await self._clear_table(DelistingEvent, "DelistingEvent (События)")

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
            
            with ui.grid(columns=3):
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
                
                # --- CMC Interval ---
                ui.label('Обновление рангов CMC').classes('text-md font-medium mt-2')
                async def on_cmc_change(e):
                    val = int(e.value)
                    await scheduler.update_cmc_interval(val)
                    ui.notify(f'Interval CMC: {val} дн.', type='positive')

                current_cmc_interval = 5
                async with get_session() as session:
                    s = await session.get(AppSettings, "cmc_update_interval_days")
                    if s: current_cmc_interval = int(s.value)
                
                # Опции для дней: 1...30
                days_options = {d: f'{d} дн.' for d in range(1, 31)}

                ui.select(options=hours_options, value=current_market_interval, on_change=on_market_change).classes('w-32')
                ui.select(options=hours_options, value=current_scraper_interval, on_change=on_scraper_change).classes('w-32')
                ui.select(options=days_options, value=current_cmc_interval, on_change=on_cmc_change).classes('w-32')

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
