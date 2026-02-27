from nicegui import ui
from sqlmodel import select, delete

from database.core import get_session
from database.models import MonitoredPair, MarketData, DelistingEvent, Signal
from services.file_watcher import FileWatcherService
from services.system import get_scraper_service, get_scheduler
from ui.layout import create_header


class ManualControlsPage:
    def __init__(self):
        self.is_syncing = False
        self.is_updating_ohlcv = False
        self.is_checking_news = False
        self.is_updating_ranks = False
        self.stats_label = None

    async def run_sync(self, button):
        """Ручной запуск синхронизации"""
        self.is_syncing = True
        button.props('loading')
        ui.notify('Запуск синхронизации...', type='info')
        matches = 0
        stats = {}
        try:
            watcher = FileWatcherService(get_session)
            stats = await watcher.sync_from_settings()
            
            # Запускаем БЫСТРЫЙ матч с историей рисков сразу после синхронизации
            async with get_session() as session:
                scraper = get_scraper_service()
                matches = await scraper.match_monitored_pairs_with_events(session)
                
            try:
                # Проверяем наличие ошибок (отсутствующие файлы)
                missing = stats.get("missing_files", [])
                if missing:
                    ui.notify(f'Внимание! Не найдены файлы: {", ".join(missing)}', type='negative', timeout=10000)
                
                # Формируем строку статистики для уведомления
                stats_str = f"Added: {stats.get('added', 0)}, Reactivated: {stats.get('reactivated', 0)}, Archived: {stats.get('archived', 0)}"
                ui.notify(f'Синхронизация завершена! {stats_str}. Найдено совпадений: {matches}', type='positive')
                
                if self.stats_label:
                    self.stats_label.text = f"{stats_str} | Matches: {matches}"
            except:
                pass
        finally:
            self.is_syncing = False
            try:
                button.props(remove='loading')
            except:
                pass

    async def run_ohlcv_update(self, scheduler, button):
        """Ручной запуск обновления цен"""
        self.is_updating_ohlcv = True
        button.props('loading')
        ui.notify('Обновление цен запущено...', type='info')
        try:
            await scheduler.market_service.update_all()
            try:
                ui.notify('Цены обновлены!', type='positive')
            except:
                pass
        finally:
            self.is_updating_ohlcv = False
            try:
                button.props(remove='loading')
            except:
                pass

    async def run_scraper_check(self, scheduler, button):
        """Ручной запуск проверки новостей"""
        self.is_checking_news = True
        button.props('loading')
        ui.notify('Запущена проверка новостей на Delist/ST...', type='info')
        try:
            await scheduler.scraper_service.check_all_risks()
            try:
                ui.notify('Проверка новостей завершена!', type='positive')
            except:
                pass
        finally:
            self.is_checking_news = False
            try:
                button.props(remove='loading')
            except:
                pass
            
    async def run_cmc_update(self, scheduler, button):
        """Ручной запуск обновления рангов CMC"""
        self.is_updating_ranks = True
        button.props('loading')
        ui.notify('Обновление рангов CMC запущено...', type='info')
        try:
            # Используем сервис из планировщика или через get_cmc_service
            msg = await scheduler.cmc_service.sync_ranks()
            try:
                ui.notify(f'Готово: {msg}', type='positive')
            except:
                pass
        finally:
            self.is_updating_ranks = False
            try:
                button.props(remove='loading')
            except:
                pass

    async def _clear_table(self, model, name_ru):
        """Generic method to clear a table"""
        async with get_session() as session:
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

    async def render(self):
        scheduler = get_scheduler()

        with ui.card().classes('w-full max-w-3xl mx-auto p-4'):
            ui.label('Принудительный запуск').classes('text-2xl font-bold mb-4')

            # --- Manual Actions ---
            ui.label().classes('text-lg font-bold')
            with ui.column().classes('gap-2 w-full'):
                
                # 1. Sync
                with ui.row().classes('w-full items-center justify-between p-4 bg-gray-50 rounded border'):
                    with ui.column().classes('gap-1'):
                        ui.label('1. Синхронизация отслеживаемых пар').classes('font-bold')
                        ui.label('Синхронизирует пары и проверяет совпадение с уже известными Delisting/ST').classes('text-xs text-gray-500')
                    btn_sync = ui.button('Синхронизировать', on_click=lambda: self.run_sync(btn_sync)).props('color=grey dense size=md icon=sync')

                # 2. OHLCV
                with ui.row().classes('w-full items-center justify-between p-4 bg-gray-50 rounded border'):
                    with ui.column().classes('gap-1'):
                        ui.label('2. Обновить OHLCv').classes('font-bold')
                        ui.label('Скачивает OHLCv для всех активных пар и запускает проверку на Pump/Dump и объемы').classes('text-xs text-gray-500')
                    btn_ohlcv = ui.button('Скачать OHLCv', on_click=lambda: self.run_ohlcv_update(scheduler, btn_ohlcv)).props('color=green dense size=md icon=download')

                # 3. Scraper
                with ui.row().classes('w-full items-center justify-between p-4 bg-gray-50 rounded border'):
                    with ui.column().classes('gap-1'):
                        ui.label('3. Проверка новостей (Delisting/ST)').classes('font-bold')
                        ui.label('Запускает скраперы Gate.io/MEXC/Binance и API проверки').classes('text-xs text-gray-500')
                    
                    btn_check = ui.button('Проверить риски', on_click=lambda: self.run_scraper_check(scheduler, btn_check)).props('color=orange dense size=md icon=bug_report')

                # 4. CMC Ranks
                with ui.row().classes('w-full items-center justify-between p-4 bg-gray-50 rounded border'):
                    with ui.column().classes('gap-1'):
                        ui.label('4. Обновление рангов CMC').classes('font-bold')
                        ui.label('Загружает ранги монет с CoinMarketCap для активных пар').classes('text-xs text-gray-500')
                    
                    btn_ranks = ui.button('Обновить ранги', on_click=lambda: self.run_cmc_update(scheduler, btn_ranks)).props('color=purple dense size=md icon=leaderboard')
            
            # --- Danger Zone ---
            ui.separator().classes('my-4')
            ui.label('Опасная зона (Удаление данных)').classes('text-xl font-bold text-red-600')
            
            with ui.row().classes('gap-2'):
                ui.button('Очистить отслеживаемые пары', on_click=self.clear_monitored_pairs).props('outline color=red')
                ui.button('Очистить OHLCv', on_click=self.clear_market_data).props('outline color=red')
                ui.button('Очистить Делистинги/ST', on_click=self.clear_delistings).props('outline color=red')
                ui.button('Очистить сигналы', on_click=self.clear_signals).props('outline color=red')


@ui.page('/manual')
async def manual_controls_page():
    create_header()
    page = ManualControlsPage()
    await page.render()
