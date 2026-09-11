import sqlite3
import os
import sys

DB_PATH = "database.db"

def migrate():
    """
    Миграция базы данных:
    1. Добавляет колонку 'market_type' в таблицу 'monitoredpair', если она отсутствует.
    2. Приводит все значения 'market_type' к верхнему регистру ('SPOT', 'LINEAR', 'INVERSE') для совпадения с Именем Enum в SQLModel.
    3. Гарантирует, что уникальное ограничение составное: (exchange, symbol, market_type),
       а не устаревшее (exchange, symbol).
    """
    if not os.path.exists(DB_PATH):
        print(f"База данных '{DB_PATH}' не найдена. Миграция не требуется.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Отключаем внешние ключи во время перестройки таблицы
        cursor.execute("PRAGMA foreign_keys=OFF;")

        cursor.execute("PRAGMA table_info(monitoredpair);")
        columns = [column[1] for column in cursor.fetchall()]

        if not columns:
            print("Таблица 'monitoredpair' еще не создана. Миграция не требуется.")
            return

        # 1. Если нет колонки market_type — добавляем
        if "market_type" not in columns:
            print("Колонка 'market_type' отсутствует в 'monitoredpair'. Добавляем...")
            cursor.execute("ALTER TABLE monitoredpair ADD COLUMN market_type VARCHAR DEFAULT 'SPOT';")

        # 2. Нормализуем значения market_type в верхний регистр (соответствие Enum Name в SQLModel)
        cursor.execute("UPDATE monitoredpair SET market_type = UPPER(market_type) WHERE market_type IS NOT NULL;")

        # 3. Проверяем текущее определение таблицы
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='monitoredpair';")
        create_sql = cursor.fetchone()
        table_sql = create_sql[0] if create_sql else ""

        # Нам нужно пересоздать таблицу, если в ограничениях НЕТ market_type
        # (например: UNIQUE (exchange, symbol) вместо UNIQUE (exchange, symbol, market_type))
        needs_table_recreation = ("UNIQUE (exchange, symbol)" in table_sql or "UNIQUE(exchange, symbol)" in table_sql) and \
                                 ("market_type" not in table_sql.rsplit("UNIQUE", 1)[-1] if "UNIQUE" in table_sql else True)

        if needs_table_recreation:
            print("Обнаружено устаревшее ограничение UNIQUE (exchange, symbol). Пересоздаем таблицу monitoredpair...")

            cursor.execute("""
                CREATE TABLE monitoredpair_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    market_type VARCHAR DEFAULT 'SPOT' NOT NULL,
                    source_file VARCHAR NOT NULL,
                    source_label VARCHAR,
                    monitoring_status VARCHAR DEFAULT 'ACTIVE' NOT NULL,
                    risk_level VARCHAR DEFAULT 'NORMAL' NOT NULL,
                    cmc_rank INTEGER,
                    volume_cv INTEGER,
                    CONSTRAINT unique_exchange_symbol_market UNIQUE (exchange, symbol, market_type)
                );
            """)

            cursor.execute("""
                INSERT INTO monitoredpair_new (id, exchange, symbol, market_type, source_file, source_label, monitoring_status, risk_level, cmc_rank, volume_cv)
                SELECT id, exchange, symbol, UPPER(market_type), source_file, source_label, UPPER(monitoring_status), UPPER(risk_level), cmc_rank, volume_cv
                FROM monitoredpair;
            """)

            cursor.execute("DROP TABLE monitoredpair;")
            cursor.execute("ALTER TABLE monitoredpair_new RENAME TO monitoredpair;")

            # Пересоздаем индексы
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_monitoredpair_exchange ON monitoredpair (exchange);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_monitoredpair_symbol ON monitoredpair (symbol);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_monitoredpair_market_type ON monitoredpair (market_type);")

            print("[OK] Таблица 'monitoredpair' успешно пересоздана со значением UNIQUE (exchange, symbol, market_type).")
        else:
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_monitoredpair_market_type ON monitoredpair (market_type);")
            print("[OK] Миграция проверена: структура таблицы 'monitoredpair' корректна.")

        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Ошибка при выполнении миграции: {e}")
        sys.exit(1)
    finally:
        cursor.execute("PRAGMA foreign_keys=ON;")
        conn.close()

if __name__ == "__main__":
    migrate()
