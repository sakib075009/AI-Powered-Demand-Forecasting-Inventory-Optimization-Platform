"""Structural and constraint tests for the Project 02 SQLite schema."""
import sqlite3

import pytest

from src.db_init import EXPECTED_TABLES, connect_database, initialize_database


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "test_project02.db"
    initialize_database(path)
    connection = connect_database(path)
    try:
        yield connection
    finally:
        connection.close()


def add_minimum_master_data(db):
    db.execute("INSERT INTO products VALUES (1, 'SKU-001', 'Test product', 'Test', 100, 150, 'piece', 1)")
    db.execute("INSERT INTO locations VALUES (1, 'LOC-001', 'Test location', 'warehouse', 'North', 1)")
    db.execute("INSERT INTO suppliers VALUES (1, 'SUP-001', 'Test supplier', NULL, 5, 1)")
    db.commit()


def test_all_14_tables_exist(db):
    tables = {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == EXPECTED_TABLES
    assert len(tables) == 14


def test_foreign_keys_are_enabled_for_each_connection(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_foreign_key_rejects_missing_product(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO demand_history (product_id, location_id, demand_date, units_demanded, units_sold, stockout_flag, estimated_lost_demand, sales_revenue) VALUES (999, 999, '2026-01-01', 1, 1, 0, 0, 100)"
        )


def test_product_required_fields_and_nonnegative_cost(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO products VALUES (1, '', 'Name', 'Category', 0, 0, 'piece', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO products VALUES (1, 'SKU-1', 'Name', 'Category', -1, 0, 'piece', 1)")


def test_inventory_reserved_cannot_exceed_on_hand(db):
    add_minimum_master_data(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO inventory_snapshots (product_id, location_id, snapshot_time, on_hand_qty, reserved_qty, on_order_qty, backorder_qty) VALUES (1, 1, '2026-01-01T00:00:00Z', 5, 6, 0, 0)"
        )


def test_purchase_order_line_quantity_reconciliation_check(db):
    add_minimum_master_data(db)
    db.execute("INSERT INTO purchase_orders (purchase_order_id, supplier_id, order_date, status) VALUES (1, 1, '2026-01-01', 'ordered')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO purchase_order_lines (purchase_order_id, product_id, ordered_qty, received_qty, cancelled_qty, unit_cost) VALUES (1, 1, 10, 7, 4, 100)"
        )


def test_inventory_movement_type_and_sign_must_agree(db):
    add_minimum_master_data(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO inventory_movements (product_id, location_id, movement_time, movement_type, quantity_delta) VALUES (1, 1, '2026-01-01T00:00:00Z', 'sale', 2)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO inventory_movements (product_id, location_id, movement_time, movement_type, quantity_delta) VALUES (1, 1, '2026-01-01T00:00:00Z', 'unknown_type', -2)"
        )


def test_unique_demand_grain_is_enforced(db):
    add_minimum_master_data(db)
    values = (1, 1, '2026-01-01', 5, 4, 0, 1, 600)
    db.execute("INSERT INTO demand_history (product_id, location_id, demand_date, units_demanded, units_sold, stockout_flag, estimated_lost_demand, sales_revenue) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO demand_history (product_id, location_id, demand_date, units_demanded, units_sold, stockout_flag, estimated_lost_demand, sales_revenue) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)


def test_delete_restricts_referenced_master_record(db):
    add_minimum_master_data(db)
    db.execute("INSERT INTO demand_history (product_id, location_id, demand_date, units_demanded, units_sold, stockout_flag, estimated_lost_demand, sales_revenue) VALUES (1, 1, '2026-01-01', 1, 1, 0, 0, 100)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM products WHERE product_id = 1")
