"""Tests for application-level Project 02 data integrity rules."""
import sqlite3

import pytest

from src.db_init import initialize_database, connect_database
from src.db_integrity import (
    require_finite,
    validate_database_integrity,
    validate_purchase_order_reconciliation,
    validate_supplier_product_periods,
)


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "integrity.db"
    initialize_database(path)
    connection = connect_database(path)
    connection.execute(
        "INSERT INTO products VALUES (1, 'SKU-001', 'Test product', 'Test', 100, 150, 'piece', 1)"
    )
    connection.execute(
        "INSERT INTO locations VALUES (1, 'LOC-001', 'Test location', 'warehouse', 'North', 1)"
    )
    connection.execute(
        "INSERT INTO suppliers VALUES (1, 'SUP-001', 'Test supplier', NULL, 5, 1)"
    )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def add_supplier_product(db, supplier_product_id, start, end=None):
    db.execute(
        """
        INSERT INTO supplier_products
        (supplier_product_id, supplier_id, product_id, unit_purchase_cost,
         lead_time_days, min_order_qty, order_multiple, is_preferred,
         effective_from, effective_to)
        VALUES (?, 1, 1, 100, 5, 1, 1, 1, ?, ?)
        """,
        (supplier_product_id, start, end),
    )
    db.commit()


def add_order(db, order_id, status, ordered=10, received=0, cancelled=0):
    db.execute(
        "INSERT INTO purchase_orders (purchase_order_id, supplier_id, order_date, status) VALUES (?, 1, '2026-01-01', ?)",
        (order_id, status),
    )
    db.execute(
        """
        INSERT INTO purchase_order_lines
        (purchase_order_id, product_id, ordered_qty, received_qty, cancelled_qty, unit_cost)
        VALUES (?, 1, ?, ?, ?, 100)
        """,
        (order_id, ordered, received, cancelled),
    )
    db.commit()


def test_supplier_product_non_overlapping_adjacent_periods_pass(db):
    add_supplier_product(db, 1, "2026-01-01", "2026-01-31")
    add_supplier_product(db, 2, "2026-02-01", None)
    assert validate_supplier_product_periods(db) == []


def test_supplier_product_inclusive_overlap_is_detected(db):
    add_supplier_product(db, 1, "2026-01-01", "2026-01-31")
    add_supplier_product(db, 2, "2026-01-31", "2026-02-28")
    errors = validate_supplier_product_periods(db)
    assert any("overlaps inclusive effective period" in error for error in errors)


def test_supplier_product_open_ended_period_blocks_later_period(db):
    add_supplier_product(db, 1, "2026-01-01", None)
    add_supplier_product(db, 2, "2026-03-01", None)
    assert any("overlaps inclusive effective period" in error
               for error in validate_supplier_product_periods(db))


def test_cancelled_purchase_order_must_reconcile_all_quantities(db):
    add_order(db, 1, "cancelled", ordered=10, received=3, cancelled=2)
    errors = validate_purchase_order_reconciliation(db)
    assert any("must reconcile" in error for error in errors)


def test_cancelled_purchase_order_with_reconciled_quantities_passes(db):
    add_order(db, 1, "cancelled", ordered=10, received=3, cancelled=7)
    assert validate_purchase_order_reconciliation(db) == []


def test_received_purchase_order_must_be_fully_received(db):
    add_order(db, 1, "received", ordered=10, received=9, cancelled=0)
    errors = validate_purchase_order_reconciliation(db)
    assert any("must be fully received" in error for error in errors)


def test_ordered_purchase_order_cannot_have_received_quantity(db):
    add_order(db, 1, "ordered", ordered=10, received=1, cancelled=0)
    errors = validate_purchase_order_reconciliation(db)
    assert any("is ordered but line" in error for error in errors)


def test_partially_received_order_requires_received_and_outstanding_quantity(db):
    add_order(db, 1, "partially_received", ordered=10, received=10, cancelled=0)
    errors = validate_purchase_order_reconciliation(db)
    assert any("must have both received and outstanding" in error for error in errors)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numeric_values_are_rejected(value):
    with pytest.raises(ValueError, match="finite number"):
        require_finite(value, "forecast_qty")


@pytest.mark.parametrize("value", [0, 1.25, 100.0])
def test_finite_numeric_values_are_accepted(value):
    assert require_finite(value, "forecast_qty") == float(value)


def test_clean_database_passes_integrity_checks(db):
    assert validate_database_integrity(db) == []
