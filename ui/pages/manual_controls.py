import asyncio
from nicegui import ui
from loguru import logger
from sqlmodel import select, delete

from database.core import get_session
from database.models import AppSettings, MonitoredPair, MarketData, DelistingEvent, Signal
from services.file_watcher import FileWatcherService
from services.system import get_scraper_service, get_scheduler
from ui.layout import create_header
import json


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
        ui.notify('Запуск синхронизации...', type='info')
        try:
            watcher = FileWatcherService(get_session)
            stats = await watcher.sync_from_settings()
            
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
            
    async def run_cmc_update(self, scheduler):
        """Ручной запуск обновления рангов CMC"""
        self.is_updating_ranks = True
        ui.notify('Обновление рангов CMC запущено...', type='info')
        try:
            # Используем сервис из планировщика или через get_cmc_service
            msg = await scheduler.cmc_service.sync_ranks()
            ui.notify(f'Готово: {msg}', type='positive')
        finally:
            self.is_updating_ranks = False

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
            with ui.row().classes('gap-2'):
                btn_sync = ui.button('Синхронизация отслеживаемых пар', 
                                    on_click=lambda: self.run_sync(btn_sync), 
                                    color='grey').props('dense size=md')
                btn_sync.bind_enabled_from(self, 'is_syncing', backward=lambda x: not x)

                btn_ohlcv = ui.button('Обновить OHLCv', 
                                     on_click=lambda: self.run_ohlcv_update(scheduler), 
                                     color='grey').props('dense size=md')
                btn_ohlcv.bind_enabled_from(self, 'is_updating_ohlcv', backward=lambda x: not x)

                with ui.row().classes('w-full items-center justify-between p-4 bg-gray-50 rounded border'):
                    with ui.column().classes('gap-1'):
                        ui.label('3. Проверка новостей (Delisting/ST)').classes('font-bold')
                        ui.label('Запускает скраперы Gate.io/MEXC и API проверки').classes('text-xs text-gray-500')
                    
                    ui.button('Проверить риски', on_click=lambda: self.run_scraper_check(scheduler)).props('color=orange icon=bug_report').bind_loading(self, 'is_checking_news')

                # CMC Ranks
                with ui.row().classes('w-full items-center justify-between p-4 bg-gray-50 rounded border'):
                    with ui.column().classes('gap-1'):
                        ui.label('4. Обновление рангов CMC').classes('font-bold')
                        ui.label('Загружает ранги (Top 100/500) для активных пар').classes('text-xs text-gray-500')
                    
                    ui.button('Обновить ранги', on_click=lambda: self.run_cmc_update(scheduler)).props('color=purple icon=leaderboard').bind_loading(self, 'is_updating_ranks')
            
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
