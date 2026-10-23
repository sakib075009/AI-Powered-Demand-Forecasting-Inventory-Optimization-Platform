"""Initialize and inspect the Project 02 SQLite database."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "database" / "project02.db"
EXPECTED_TABLES = {
    "products", "locations", "suppliers", "supplier_products", "demand_history",
    "inventory_snapshots", "inventory_movements", "purchase_orders", "purchase_order_lines",
    "forecast_runs", "forecast_results", "forecast_evaluations", "planning_runs",
    "replenishment_recommendations",
}


def connect_database(database_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with foreign-key enforcement enabled."""
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    enabled = connection.execute("PRAGMA foreign_keys;").fetchone()[0]
    if enabled != 1:
        connection.close()
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")
    return connection


def initialize_database(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
) -> Path:
    """Create the schema in a database file; existing data is not dropped."""
    database_path = Path(database_path)
    schema_path = Path(schema_path)
    if not schema_path.is_file():
        raise FileNotFoundError(f"Schema SQL file not found: {schema_path}")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    sql = schema_path.read_text(encoding="utf-8")
    with connect_database(database_path) as connection:
        connection.executescript(sql)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
            )
        }
        missing = EXPECTED_TABLES - tables
        if missing:
            raise RuntimeError(f"Database initialization is missing tables: {sorted(missing)}")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check;").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"Foreign-key check failed: {[tuple(row) for row in foreign_key_errors]}")
    return database_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the Project 02 SQLite database.")
    parser.add_argument("--db", default=str(DEFAULT_DATABASE_PATH), help="Database file path")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH), help="Schema SQL path")
    args = parser.parse_args()
    database_path = initialize_database(args.db, args.schema)
    with connect_database(database_path) as connection:
        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
        ).fetchone()[0]
        print(f"Database initialized: {database_path}")
        print(f"User tables found: {table_count}")
        print(f"Foreign-key enforcement: {connection.execute('PRAGMA foreign_keys;').fetchone()[0]}")
        print(f"Foreign-key violations: {len(connection.execute('PRAGMA foreign_key_check;').fetchall())}")


if __name__ == "__main__":
    main()
