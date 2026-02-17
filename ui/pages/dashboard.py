import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from sqlmodel import select
from nicegui import ui

from database.core import get_session
from database.models import MonitoredPair, MarketData, RiskLevel, MonitoringStatus, DelistingEvent, Signal, SignalType, AppSettings
from ui.layout import create_header

async def get_dashboard_data() -> Dict[str, Any]:
    """
    Собирает данные для таблицы и статистику.
    """
    async with get_session() as session:
        # 1. Получаем все активные пары
        stmt = select(MonitoredPair).where(MonitoredPair.monitoring_status == MonitoringStatus.ACTIVE)
        pairs = (await session.execute(stmt)).scalars().all()
        
        # Загружаем настройки CMC Rank Threshold
        rank_threshold = 999999
        try:
            rt_obj = await session.get(AppSettings, "cmc_rank_threshold")
            if rt_obj and rt_obj.value:
                rank_threshold = int(rt_obj.value)
        except: pass

        data_rows = []
        stats = {"total": len(pairs), "risk": 0, "delist": 0}
        
        # Кэшируем последние цены для оптимизации (одним запросом сложно из-за group by, но можно)
        # Пока оставим в цикле, но оптимизируем запросы алертов
        
        # Поиск недавних алертов для всех пар сразу (за последние 10 дней)
        alerts_cutoff = datetime.now(timezone.utc) - timedelta(days=10)
        
        # Получаем все сигналы за период
        signals_stmt = select(Signal).where(Signal.created_at >= alerts_cutoff)
        recent_signals = (await session.execute(signals_stmt)).scalars().all()
        
        # Группируем сигналы по pair_id (если есть) или по тексту
        price_alerts_map = set()
        volume_alerts_map = set()
        
        for sig in recent_signals:
            # Простая эвристика по тексту, так как pair_id может быть не заполнен в старых записях
            # Но лучше использовать pair_id если есть
            if sig.type == SignalType.PRICE_CHANGE:
                if sig.pair_id: price_alerts_map.add(sig.pair_id)
            elif sig.type == SignalType.VOLUME_ALERT:
                if sig.pair_id: volume_alerts_map.add(sig.pair_id)

        for pair in pairs:
            # Статистика
            if pair.risk_level == RiskLevel.DELISTING_PLANNED:
                stats["delist"] += 1
            elif pair.risk_level != RiskLevel.NORMAL:
                stats["risk"] += 1

            # Ищем последнюю цену
            price_stmt = select(MarketData).where(MarketData.pair_id == pair.id).order_by(MarketData.timestamp.desc()).limit(1)
            last_price = (await session.execute(price_stmt)).scalar_one_or_none()
            
            # Распаковка labels
            try:
                labels = json.loads(pair.source_label) if pair.source_label else []
                labels_str = ", ".join(labels) if isinstance(labels, list) else str(pair.source_label)
            except:
                labels_str = pair.source_label or ""

            price_val = last_price.close if last_price else 0.0
            updated_at = last_price.timestamp.strftime("%d.%m %H:%M") if last_price else "—"
            
            # Поиск ссылки на статью для рисковых статусов
            announcement_url = None
            if pair.risk_level != RiskLevel.NORMAL:
                base_currency = pair.symbol.split('/')[0]
                event_stmt = select(DelistingEvent).where(
                    DelistingEvent.symbol == base_currency
                ).order_by(DelistingEvent.found_at.desc()).limit(1)
                
                event = (await session.execute(event_stmt)).scalar_one_or_none()
                if event:
                    announcement_url = event.announcement_url

            # Цвета для статуса риска
            risk_color = "text-gray-400"
            if pair.risk_level == RiskLevel.DELISTING_PLANNED:
                risk_color = "text-red-600 font-bold"
            elif pair.risk_level in [RiskLevel.RISK_ZONE, RiskLevel.CROSS_DELISTING]:
                risk_color = "text-orange-600 font-bold"
            elif pair.risk_level in [RiskLevel.CROSS_RISK]:
                risk_color = "text-yellow-600 font-medium"

            # Алерты (используем map)
            has_price_alert = pair.id in price_alerts_map
            has_volume_alert = pair.id in volume_alerts_map

            # Rank Logic
            rank_val = pair.cmc_rank
            rank_display = str(rank_val) if rank_val else "—"
            rank_color = "text-gray-400"
            if rank_val:
                if rank_val <= 100:
                    rank_color = "text-green-600 font-bold"
                elif rank_val > rank_threshold:
                    rank_color = "text-red-600 font-bold"
                else:
                    rank_color = "text-black-500 font-bold"

            data_rows.append({
                "id": pair.id,
                "exchange": pair.exchange,
                "symbol": pair.symbol,
                "price": f"{price_val:.8f}".rstrip('0').rstrip('.') if price_val > 0 else "N/A",
                "rank": rank_display,
                "rank_color": rank_color,
                "risk_level": pair.risk_level.value.upper().replace("_", " "),
                "risk_color": risk_color,
                "announcement_url": announcement_url,
                "labels": labels_str,
                "labels_count": len(labels) if isinstance(labels, list) else 1,
                "updated": updated_at,
                "tv_url": f"https://www.tradingview.com/chart/?symbol={pair.exchange.upper()}:{pair.symbol.replace('/', '')}",
                "has_price_alert": has_price_alert,
                "has_volume_alert": has_volume_alert
            })
        
        return {"rows": data_rows, "stats": stats}

@ui.page('/')
async def dashboard_page():
    create_header()
    
    # Локальное состояние страницы
    state = {
        "full_data": [],
        "filter_exchange": "Все",
        "filter_status": "Все",
        "search_text": ""
    }
    
    # UI элементы, которые нужно обновлять
    stats_labels = {}
    table_ref = None

    async def refresh_data():
        data = await get_dashboard_data()
        state["full_data"] = data["rows"]
        
        # Обновляем статистику
        stats = data["stats"]
        stats_labels['total'].text = str(stats['total'])
        stats_labels['risk'].text = str(stats['risk'])
        stats_labels['delist'].text = str(stats['delist'])
        
        # Обновляем фильтры (опции)
        exchanges = ['Все'] + sorted(list(set(r['exchange'] for r in state["full_data"])))
        statuses = ['Все', 'Все кроме NORMAL'] + sorted(list(set(r['risk_level'] for r in state["full_data"])))
        
        ex_select.options = exchanges
        st_select.options = statuses
        
        apply_filters()
        ui.notify('Данные обновлены', type='info')

    def apply_filters():
        filtered = state["full_data"]
        
        if state["filter_exchange"] != 'Все':
            filtered = [r for r in filtered if r['exchange'] == state["filter_exchange"]]
            
        if state["filter_status"] == 'Все кроме NORMAL':
            filtered = [r for r in filtered if r['risk_level'] != 'NORMAL']
        elif state["filter_status"] != 'Все':
            filtered = [r for r in filtered if r['risk_level'] == state["filter_status"]]
            
        if state["search_text"]:
            search = state["search_text"].lower()
            filtered = [r for r in filtered if search in str(r.values()).lower()]

        if table_ref:
            table_ref.rows = filtered

    def reset_filters():
        state["filter_exchange"] = 'Все'
        state["filter_status"] = 'Все'
        state["search_text"] = ''
        
        ex_select.value = 'Все'
        st_select.value = 'Все'
        search_input.value = ''
        
        apply_filters()

    # --- UI Layout ---
    with ui.column().classes('w-full p-2 bg-gray-50 min-h-screen gap-2'):
        
        # Верхняя панель: Статистика + Фильтры
        with ui.row().classes('w-full gap-2 items-center wrap'):
            # Карточки статистики
            with ui.row().classes('gap-2'):
                with ui.card().classes('p-2 bg-white shadow-sm border-l-4 border-blue-500'):
                    with ui.row().classes('items-center gap-2'):
                        ui.label('Пар:').classes('text-xs text-gray-500')
                        stats_labels['total'] = ui.label('...').classes('text-xl font-bold')
                
                with ui.card().classes('p-2 bg-white shadow-sm border-l-4 border-orange-500'):
                    with ui.row().classes('items-center gap-2'):
                        ui.label('ST:').classes('text-xs text-gray-500')
                        stats_labels['risk'] = ui.label('...').classes('text-xl font-bold text-orange-600')

                with ui.card().classes('p-2 bg-white shadow-sm border-l-4 border-red-500'):
                    with ui.row().classes('items-center gap-2'):
                        ui.label('Delist:').classes('text-xs text-gray-500')
                        stats_labels['delist'] = ui.label('...').classes('text-xl font-bold text-red-600')

            ui.space()

            # Фильтры
            search_input = ui.input(placeholder='Поиск...').classes('w-48').props('dense outlined')
            search_input.on('update:model-value', lambda e: [state.update({"search_text": e.args}), apply_filters()])

            ex_select = ui.select(['Все'], label='Биржа', value='Все').classes('w-28').props('dense outlined')
            ex_select.on('update:model-value', lambda e: [state.update({"filter_exchange": e.args}), apply_filters()])

            st_select = ui.select(['Все'], label='Статус', value='Все').classes('w-36').props('dense outlined')
            st_select.on('update:model-value', lambda e: [state.update({"filter_status": e.args}), apply_filters()])
            
            ui.button(icon='restart_alt', on_click=reset_filters).props('flat round dense')

        # Кнопка обновления
        ui.button('Обновить данные', on_click=refresh_data, icon='refresh').props('rounded outline').classes('mb-2')

        # Таблица
        with ui.card().classes('w-full bg-white shadow-md'):
            ui.label('Активные пары').classes('text-xl font-bold p-4 border-b w-full')
            
            columns = [
                {'name': 'exchange', 'label': 'Биржа', 'field': 'exchange', 'align': 'left', 'sortable': True},
                {'name': 'symbol', 'label': 'Пара', 'field': 'symbol', 'align': 'left', 'sortable': True},
                {'name': 'rank', 'label': '#', 'field': 'rank', 'align': 'center', 'sortable': True},
                {'name': 'price', 'label': 'Цена', 'field': 'price', 'align': 'right', 'sortable': True},
                {'name': 'risk_level', 'label': 'Статус', 'field': 'risk_level', 'align': 'center', 'sortable': True},
                {'name': 'has_price_alert', 'label': '📈', 'field': 'has_price_alert', 'align': 'center', 'sortable': True},
                {'name': 'has_volume_alert', 'label': '📊', 'field': 'has_volume_alert', 'align': 'center', 'sortable': True},
                {'name': 'labels_count', 'label': 'Списков', 'field': 'labels_count', 'align': 'center', 'sortable': True},
                {'name': 'labels', 'label': 'Метки файлов', 'field': 'labels', 'align': 'left', 'sortable': True},
                {'name': 'tv_url', 'label': 'ТВ', 'field': 'tv_url', 'align': 'center'},
                {'name': 'updated', 'label': 'Обновлено', 'field': 'updated', 'align': 'right'},
            ]

            table_ref = ui.table(columns=columns, rows=[], row_key='id').classes('w-full sticky-header')
            table_ref.props('flat bordered wrap-cells')
            
            # Слоты для кастомного рендеринга
            table_ref.add_slot('body-cell-risk_level', '''
                <q-td :props="props">
                    <template v-if="props.row.announcement_url">
                        <a :href="props.row.announcement_url" target="_blank" class="no-underline">
                            <q-badge :class="props.row.risk_color" outline class="cursor-pointer hover:bg-gray-100">
                                {{ props.value }}
                                <q-icon name="open_in_new" size="xs" class="q-ml-xs" />
                            </q-badge>
                        </a>
                    </template>
                    <template v-else>
                        <q-badge :class="props.row.risk_color" outline>
                            {{ props.value }}
                        </q-badge>
                    </template>
                </q-td>
            ''')

            table_ref.add_slot('body-cell-rank', '''
                <q-td :props="props">
                    <span :class="props.row.rank_color">{{ props.value }}</span>
                </q-td>
            ''')

            table_ref.add_slot('body-cell-tv_url', '''
                <q-td :props="props">
                    <a :href="props.value" target="_blank" class="no-underline text-blue-600">
                        <q-btn flat round dense icon="show_chart" color="primary">
                            <q-tooltip>Открыть график на TradingView</q-tooltip>
                        </q-btn>
                    </a>
                </q-td>
            ''')

            table_ref.add_slot('body-cell-has_price_alert', '''
                <q-td :props="props">
                    <q-icon v-if="props.value" name="warning" color="orange">
                        <q-tooltip>Алерт по цене (10 дн)</q-tooltip>
                    </q-icon>
                    <span v-else class="text-gray-300">—</span>
                </q-td>
            ''')

            table_ref.add_slot('body-cell-has_volume_alert', '''
                <q-td :props="props">
                    <q-icon v-if="props.value" name="warning" color="purple">
                        <q-tooltip>Алерт по объему (10 дн)</q-tooltip>
                    </q-icon>
                    <span v-else class="text-gray-300">—</span>
                </q-td>
            ''')

    # Загружаем данные при открытии страницы
    await refresh_data()
