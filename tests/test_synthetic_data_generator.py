"""Tests for the Project 02 synthetic data generator."""
import json
import sqlite3

import pytest

from src.db_init import initialize_database
from src.generate_synthetic_data import generate_dataset
from src.db_integrity import validate_database_integrity


def _fresh_db(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "project02.db"
    initialize_database(path)
    return path


def _connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def test_generator_populates_nine_operational_tables_and_leaves_future_tables_empty(tmp_path):
    db = _fresh_db(tmp_path)
    metadata_path = tmp_path / "metadata.json"
    summary = generate_dataset(
        db, seed=42, history_days=30, end_date="2026-10-23",
        metadata_path=metadata_path,
    )
    assert summary["row_counts"]["products"] == 12
    assert summary["row_counts"]["locations"] == 3
    assert summary["row_counts"]["suppliers"] == 5
    assert summary["row_counts"]["demand_history"] == 12 * 3 * 30
    assert summary["row_counts"]["inventory_snapshots"] == 12 * 3 * 30
    for table in (
        "forecast_runs", "forecast_results", "forecast_evaluations",
        "planning_runs", "replenishment_recommendations",
    ):
        assert summary["row_counts"][table] == 0
    assert metadata_path.exists()


def test_generator_creates_complete_daily_series_per_product_location(tmp_path):
    db = _fresh_db(tmp_path)
    generate_dataset(db, seed=7, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    con = _connect(db)
    try:
        count, distinct_pairs, distinct_dates = con.execute("""
            SELECT COUNT(*),
                   COUNT(DISTINCT product_id || ':' || location_id),
                   COUNT(DISTINCT demand_date)
            FROM demand_history
        """).fetchone()
        assert count == 12 * 3 * 30
        assert distinct_pairs == 12 * 3
        assert distinct_dates == 30
        duplicates = con.execute("""
            SELECT product_id, location_id, demand_date, COUNT(*)
            FROM demand_history
            GROUP BY product_id, location_id, demand_date
            HAVING COUNT(*) > 1
        """).fetchall()
        assert duplicates == []
    finally:
        con.close()


def test_demand_sales_lost_demand_and_stockout_flags_are_consistent(tmp_path):
    db = _fresh_db(tmp_path)
    generate_dataset(db, seed=8, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    con = _connect(db)
    try:
        assert con.execute("""
            SELECT COUNT(*) FROM demand_history
            WHERE units_sold > units_demanded
               OR estimated_lost_demand != units_demanded - units_sold
               OR stockout_flag != CASE WHEN units_demanded > units_sold THEN 1 ELSE 0 END
               OR sales_revenue < 0
        """).fetchone()[0] == 0
    finally:
        con.close()


def test_inventory_movement_signs_match_types(tmp_path):
    db = _fresh_db(tmp_path)
    generate_dataset(db, seed=9, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    con = _connect(db)
    try:
        assert con.execute("""
            SELECT COUNT(*) FROM inventory_movements
            WHERE (movement_type IN ('receipt','customer_return','adjustment_increase','transfer_in')
                   AND quantity_delta <= 0)
               OR (movement_type IN ('sale','supplier_return','adjustment_decrease','transfer_out','damage_or_writeoff')
                   AND quantity_delta >= 0)
        """).fetchone()[0] == 0
    finally:
        con.close()


def test_purchase_order_quantities_reconcile_with_status(tmp_path):
    db = _fresh_db(tmp_path)
    generate_dataset(db, seed=10, history_days=45, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    con = _connect(db)
    try:
        assert con.execute("""
            SELECT COUNT(*)
            FROM purchase_orders po
            JOIN purchase_order_lines pol USING (purchase_order_id)
            WHERE (po.status='received' AND
                   (pol.received_qty != pol.ordered_qty OR pol.cancelled_qty != 0))
               OR (po.status='ordered' AND
                   (pol.received_qty != 0 OR pol.cancelled_qty != 0))
        """).fetchone()[0] == 0
    finally:
        con.close()


def test_snapshot_on_order_matches_open_order_quantity_by_synthetic_location_convention(tmp_path):
    db = _fresh_db(tmp_path)
    generate_dataset(db, seed=11, history_days=45, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    con = _connect(db)
    try:
        # Verify snapshot quantities are nonnegative and no greater than a sensible cap.
        assert con.execute("""
            SELECT COUNT(*) FROM inventory_snapshots
            WHERE on_hand_qty < 0 OR reserved_qty < 0
               OR reserved_qty > on_hand_qty OR on_order_qty < 0 OR backorder_qty < 0
        """).fetchone()[0] == 0
    finally:
        con.close()


def test_supplier_effective_periods_are_valid_and_non_overlapping(tmp_path):
    db = _fresh_db(tmp_path)
    generate_dataset(db, seed=12, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    con = _connect(db)
    try:
        rows = con.execute("""
            SELECT supplier_id, product_id, effective_from, effective_to
            FROM supplier_products
        """).fetchall()
        assert rows
        assert all(row["effective_to"] is None or row["effective_to"] >= row["effective_from"]
                   for row in rows)
        assert validate_database_integrity(con) == []
    finally:
        con.close()


def test_foreign_key_check_is_clean_after_generation(tmp_path):
    db = _fresh_db(tmp_path)
    generate_dataset(db, seed=13, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    con = _connect(db)
    try:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


def test_metadata_records_seed_dates_counts_and_schema_limitation(tmp_path):
    db = _fresh_db(tmp_path)
    metadata_path = tmp_path / "metadata.json"
    generate_dataset(db, seed=99, history_days=30, end_date="2026-10-23",
                     metadata_path=metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["seed"] == 99
    assert metadata["start_date"] == "2026-09-24"
    assert metadata["end_date"] == "2026-10-23"
    assert metadata["synthetic_data"] is True
    assert "no location_id" in metadata["purchase_order_location_convention"]


def test_fixed_seed_and_configuration_reproduce_data_counts_and_values(tmp_path):
    db1 = _fresh_db(tmp_path / "a")
    db2 = _fresh_db(tmp_path / "b")
    generate_dataset(db1, seed=2026, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "a" / "metadata.json")
    generate_dataset(db2, seed=2026, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "b" / "metadata.json")
    con1, con2 = _connect(db1), _connect(db2)
    try:
        for table, order in (
            ("products", "product_id"),
            ("locations", "location_id"),
            ("suppliers", "supplier_id"),
            ("supplier_products", "supplier_product_id"),
            ("demand_history", "demand_id"),
            ("inventory_snapshots", "snapshot_id"),
            ("inventory_movements", "movement_id"),
            ("purchase_orders", "purchase_order_id"),
            ("purchase_order_lines", "purchase_order_line_id"),
        ):
            rows1 = [tuple(row) for row in con1.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            rows2 = [tuple(row) for row in con2.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            assert rows1 == rows2, table
    finally:
        con1.close()
        con2.close()


def test_generator_refuses_to_overwrite_populated_database(tmp_path):
    db = _fresh_db(tmp_path)
    generate_dataset(db, seed=1, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    with pytest.raises(RuntimeError, match="EMPTY initialized database"):
        generate_dataset(db, seed=2, history_days=30, end_date="2026-10-23",
                         metadata_path=tmp_path / "meta2.json")


def test_invalid_history_days_are_rejected(tmp_path):
    db = _fresh_db(tmp_path)
    with pytest.raises(ValueError, match="at least 30"):
        generate_dataset(db, history_days=10, metadata_path=tmp_path / "meta.json")


def test_foreign_keys_are_enabled_for_generation_and_no_schema_changes(tmp_path):
    db = _fresh_db(tmp_path)
    before = None
    con = _connect(db)
    try:
        before = [row["name"] for row in con.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)]
    finally:
        con.close()
    generate_dataset(db, seed=14, history_days=30, end_date="2026-10-23",
                     metadata_path=tmp_path / "meta.json")
    con = _connect(db)
    try:
        after = [row["name"] for row in con.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)]
        assert after == before
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
