"""Data-quality audit for Project 02 synthetic operational data.

Usage from the project root:
    python -m src.validate_synthetic_data
    python -m src.validate_synthetic_data --db database/project02.db
    python -m src.validate_synthetic_data --expected-start-date 2026-04-27 --expected-end-date 2026-10-23

The validator is read-only: it does not update or delete database records.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

REQUIRED_TABLES = (
    "products", "locations", "suppliers", "supplier_products",
    "demand_history", "inventory_snapshots", "inventory_movements",
    "purchase_orders", "purchase_order_lines", "forecast_runs",
    "forecast_results", "forecast_evaluations", "planning_runs",
    "replenishment_recommendations",
)
FUTURE_TABLES = (
    "forecast_runs", "forecast_results", "forecast_evaluations",
    "planning_runs", "replenishment_recommendations",
)


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _iso_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return parsed.isoformat() == value


def validate_synthetic_data(
    connection: sqlite3.Connection,
    *,
    expected_start_date: str | None = None,
    expected_end_date: str | None = None,
) -> dict[str, Any]:
    """Return a machine-readable report with checks, errors, metrics and row counts."""
    connection.row_factory = sqlite3.Row
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    metrics: dict[str, Any] = {}

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            errors.append(f"{name}: {detail}")

    present = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = sorted(set(REQUIRED_TABLES) - present)
    record("required_tables", not missing,
           "All 14 approved tables exist." if not missing else f"Missing tables: {missing}")
    if missing:
        return {
            "status": "FAIL", "checks": checks, "errors": errors,
            "metrics": metrics, "row_counts": {},
        }

    row_counts = {table: _count(connection, table) for table in REQUIRED_TABLES}
    metrics["row_counts"] = row_counts
    fk_violations = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
    record("foreign_key_integrity", not fk_violations,
           "No foreign-key violations." if not fk_violations else f"Violations: {fk_violations[:10]}")

    # Core demand constraints and uniqueness.
    demand_bad = connection.execute("""
        SELECT COUNT(*) FROM demand_history
        WHERE units_demanded < 0 OR units_sold < 0 OR units_sold > units_demanded
           OR estimated_lost_demand < 0
           OR estimated_lost_demand != units_demanded - units_sold
           OR stockout_flag != CASE WHEN units_demanded > units_sold THEN 1 ELSE 0 END
           OR sales_revenue < 0 OR typeof(sales_revenue) != 'integer'
           OR demand_date IS NULL OR length(demand_date) != 10
    """).fetchone()[0]
    record("demand_business_rules", demand_bad == 0,
           f"Invalid demand rows: {demand_bad} (expected 0).")

    duplicate_demand = connection.execute("""
        SELECT COUNT(*) FROM (
            SELECT product_id, location_id, demand_date
            FROM demand_history GROUP BY product_id, location_id, demand_date
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    record("demand_unique_product_location_date", duplicate_demand == 0,
           f"Duplicate product-location-date groups: {duplicate_demand} (expected 0).")

    coverage = connection.execute("""
        SELECT MIN(demand_date), MAX(demand_date), COUNT(DISTINCT demand_date),
               COUNT(DISTINCT product_id || ':' || location_id), COUNT(*)
        FROM demand_history
    """).fetchone()
    start, end, distinct_dates, pair_count, demand_count = coverage
    metrics["demand_coverage"] = {
        "start_date": start, "end_date": end,
        "distinct_dates": int(distinct_dates), "product_location_pairs": int(pair_count),
        "rows": int(demand_count),
    }
    valid_date_values = connection.execute(
        "SELECT demand_date FROM demand_history WHERE demand_date IS NOT NULL"
    ).fetchall()
    malformed_dates = sum(not _iso_date(row[0]) for row in valid_date_values)
    record("demand_iso_dates", malformed_dates == 0,
           f"Malformed/noncanonical demand dates: {malformed_dates} (expected 0).")

    date_range_valid = bool(start and end and _iso_date(start) and _iso_date(end) and start <= end)
    if date_range_valid:
        day_span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        expected_rows = day_span * int(pair_count)
        coverage_complete = int(distinct_dates) == day_span and int(demand_count) == expected_rows
        detail = (f"{start} through {end}: {distinct_dates} distinct dates, {pair_count} pairs, "
                  f"{demand_count} rows; expected {day_span} dates and {expected_rows} rows.")
    else:
        day_span = 0
        coverage_complete = int(demand_count) == 0
        detail = "No valid demand date range available." if demand_count else "Demand table is empty."
    metrics["demand_coverage"]["expected_calendar_days"] = day_span
    metrics["demand_coverage"]["expected_rows_if_complete"] = day_span * int(pair_count)
    record("complete_daily_demand_coverage", coverage_complete, detail)

    for arg_name, expected, observed in (
        ("expected_start_date", expected_start_date, start),
        ("expected_end_date", expected_end_date, end),
    ):
        if expected is not None:
            valid_expected = _iso_date(expected)
            passed = valid_expected and observed == expected
            record(arg_name, passed, f"Expected {expected}; observed {observed}.")

    # Verify every pair has the same number of days as the global date span.
    pair_gaps = connection.execute("""
        SELECT product_id, location_id, COUNT(*) AS n_dates,
               COUNT(DISTINCT demand_date) AS n_distinct,
               MIN(demand_date) AS first_date, MAX(demand_date) AS last_date
        FROM demand_history GROUP BY product_id, location_id
        HAVING n_dates != ? OR n_distinct != ? OR first_date != ? OR last_date != ?
    """, (day_span, day_span, start, end)).fetchall() if date_range_valid else []
    record("daily_demand_per_pair", len(pair_gaps) == 0,
           "Every product-location pair has one row per calendar day." if not pair_gaps
           else f"Pairs with coverage gaps: {[tuple(r) for r in pair_gaps[:10]]}")

    # Inventory snapshots must cover the same date/pair grid and be nonnegative/reasonable.
    snapshot_bad = connection.execute("""
        SELECT COUNT(*) FROM inventory_snapshots
        WHERE on_hand_qty < 0 OR reserved_qty < 0 OR reserved_qty > on_hand_qty
           OR on_order_qty < 0 OR backorder_qty < 0
           OR snapshot_time IS NULL OR length(snapshot_time) < 10
    """).fetchone()[0]
    record("inventory_snapshot_bounds", snapshot_bad == 0,
           f"Invalid snapshot rows: {snapshot_bad} (expected 0).")
    snapshot_duplicates = connection.execute("""
        SELECT COUNT(*) FROM (
            SELECT product_id, location_id, substr(snapshot_time, 1, 10) AS snapshot_date
            FROM inventory_snapshots GROUP BY product_id, location_id, snapshot_date
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    record("inventory_snapshot_unique_daily", snapshot_duplicates == 0,
           f"Duplicate daily snapshot groups: {snapshot_duplicates} (expected 0).")
    snapshot_grid_bad = connection.execute("""
        SELECT COUNT(*) FROM (
            SELECT d.product_id, d.location_id, d.demand_date
            FROM demand_history d
            LEFT JOIN inventory_snapshots s
              ON s.product_id=d.product_id AND s.location_id=d.location_id
             AND substr(s.snapshot_time,1,10)=d.demand_date
            WHERE s.snapshot_id IS NULL
            UNION ALL
            SELECT s.product_id, s.location_id, substr(s.snapshot_time,1,10)
            FROM inventory_snapshots s
            LEFT JOIN demand_history d
              ON d.product_id=s.product_id AND d.location_id=s.location_id
             AND d.demand_date=substr(s.snapshot_time,1,10)
            WHERE d.demand_id IS NULL
        )
    """).fetchone()[0]
    record("demand_snapshot_date_alignment", snapshot_grid_bad == 0,
           f"Demand/snapshot date-pair mismatches: {snapshot_grid_bad} (expected 0).")

    # Movement types must have the approved sign. Sales movements must match demand rows.
    movement_bad = connection.execute("""
        SELECT COUNT(*) FROM inventory_movements
        WHERE (movement_type IN ('receipt','customer_return','adjustment_increase','transfer_in')
               AND quantity_delta <= 0)
           OR (movement_type IN ('sale','supplier_return','adjustment_decrease','transfer_out','damage_or_writeoff')
               AND quantity_delta >= 0)
           OR quantity_delta = 0
    """).fetchone()[0]
    record("inventory_movement_signs", movement_bad == 0,
           f"Invalid movement signs: {movement_bad} (expected 0).")
    bad_sales = connection.execute("""
        SELECT COUNT(*) FROM inventory_movements m
        LEFT JOIN demand_history d
          ON m.reference_type='demand_history' AND CAST(m.reference_id AS INTEGER)=d.demand_id
        WHERE m.movement_type='sale'
          AND (d.demand_id IS NULL OR m.product_id != d.product_id
               OR m.location_id != d.location_id OR m.quantity_delta != -d.units_sold)
    """).fetchone()[0]
    record("sales_movement_matches_demand", bad_sales == 0,
           f"Sales movements inconsistent with demand history: {bad_sales} (expected 0).")
    unsourced_sales = connection.execute("""
        SELECT COUNT(*) FROM demand_history d
        WHERE d.units_sold > 0 AND NOT EXISTS (
            SELECT 1 FROM inventory_movements m
            WHERE m.movement_type='sale' AND m.reference_type='demand_history'
              AND CAST(m.reference_id AS INTEGER)=d.demand_id
              AND m.quantity_delta=-d.units_sold
        )
    """).fetchone()[0]
    record("every_sale_has_inventory_movement", unsourced_sales == 0,
           f"Positive-sales rows without matching movement: {unsourced_sales} (expected 0).")

    # For each product-location, closing stock change equals that date's net movements.
    # First snapshot is a baseline because opening stock predates the recorded history.
    snapshots = connection.execute("""
        SELECT product_id, location_id, substr(snapshot_time,1,10) AS snapshot_date, on_hand_qty
        FROM inventory_snapshots ORDER BY product_id, location_id, snapshot_date
    """).fetchall()
    movement_totals = {
        (row["product_id"], row["location_id"], row["movement_date"]): row["net_delta"]
        for row in connection.execute("""
            SELECT product_id, location_id, substr(movement_time,1,10) AS movement_date,
                   SUM(quantity_delta) AS net_delta
            FROM inventory_movements GROUP BY product_id, location_id, substr(movement_time,1,10)
        """).fetchall()
    }
    previous: dict[tuple[int, int], tuple[str, int]] = {}
    reconciliation_errors: list[str] = []
    for row in snapshots:
        key = (row["product_id"], row["location_id"])
        current_date, current_qty = row["snapshot_date"], int(row["on_hand_qty"])
        if key in previous:
            prev_date, prev_qty = previous[key]
            net_delta = int(movement_totals.get((key[0], key[1], current_date), 0))
            if (date.fromisoformat(current_date) - date.fromisoformat(prev_date)).days != 1:
                reconciliation_errors.append(f"{key} has nonconsecutive snapshots {prev_date} -> {current_date}")
            elif current_qty - prev_qty != net_delta:
                reconciliation_errors.append(
                    f"{key} {current_date}: stock delta {current_qty-prev_qty}, movement net {net_delta}"
                )
        previous[key] = (current_date, current_qty)
    record("inventory_daily_reconciliation", not reconciliation_errors,
           "Daily on-hand changes reconcile to signed inventory movements after each pair's baseline snapshot."
           if not reconciliation_errors else f"Reconciliation issues ({len(reconciliation_errors)}): {reconciliation_errors[:10]}")

    # PO quantity/status checks, including multiple lines per order.
    po_errors: list[str] = []
    orders = connection.execute("SELECT purchase_order_id, status FROM purchase_orders").fetchall()
    for order in orders:
        lines = connection.execute("""
            SELECT ordered_qty, received_qty, cancelled_qty
            FROM purchase_order_lines WHERE purchase_order_id=?
        """, (order["purchase_order_id"],)).fetchall()
        if not lines:
            po_errors.append(f"Order {order['purchase_order_id']} has no lines")
            continue
        for line in lines:
            ordered, received, cancelled = line["ordered_qty"], line["received_qty"], line["cancelled_qty"]
            if min(ordered, received, cancelled) < 0 or received + cancelled > ordered:
                po_errors.append(f"Order {order['purchase_order_id']} has invalid quantities")
            if order["status"] == "received" and (received != ordered or cancelled != 0):
                po_errors.append(f"Received order {order['purchase_order_id']} is not fully reconciled")
            if order["status"] == "ordered" and (received != 0 or cancelled != 0):
                po_errors.append(f"Ordered order {order['purchase_order_id']} has received/cancelled quantity")
            if order["status"] == "cancelled" and received + cancelled != ordered:
                po_errors.append(f"Cancelled order {order['purchase_order_id']} is not fully reconciled")
    record("purchase_order_reconciliation", not po_errors,
           "Purchase-order quantities reconcile with order status." if not po_errors
           else f"Purchase-order issues ({len(po_errors)}): {po_errors[:10]}")

    # Reuse the existing application-level cross-row checks when available.
    try:
        from src.db_integrity import validate_database_integrity
        app_errors = validate_database_integrity(connection)
    except (ImportError, AttributeError):
        app_errors = []
    record("application_integrity_rules", not app_errors,
           "Application-level integrity checks passed." if not app_errors
           else f"Application integrity issues ({len(app_errors)}): {app_errors[:10]}")

    future_counts = {table: row_counts[table] for table in FUTURE_TABLES}
    metrics["future_phase_table_rows"] = future_counts
    record("future_tables_empty", all(count == 0 for count in future_counts.values()),
           "Forecasting/planning tables are empty as expected for this phase."
           if all(count == 0 for count in future_counts.values())
           else f"Expected empty forecast/planning tables, found rows: {future_counts}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "checks_passed": sum(item["passed"] for item in checks),
        "checks_failed": sum(not item["passed"] for item in checks),
        "checks": checks,
        "errors": errors,
        "metrics": metrics,
        "row_counts": row_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only data-quality audit for Project 02.")
    parser.add_argument("--db", default="database/project02.db", help="SQLite database path")
    parser.add_argument("--report", default="data/processed/data_quality_report.json",
                        help="JSON report output path")
    parser.add_argument("--expected-start-date", default=None, help="Expected demand start date (YYYY-MM-DD)")
    parser.add_argument("--expected-end-date", default=None, help="Expected demand end date (YYYY-MM-DD)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 2
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        report = validate_synthetic_data(
            connection,
            expected_start_date=args.expected_start_date,
            expected_end_date=args.expected_end_date,
        )
    except sqlite3.Error as exc:
        print(f"ERROR: database validation failed: {exc}", file=sys.stderr)
        return 2
    finally:
        connection.close()

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = report_path.with_suffix(report_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(report_path)

    print(f"Data quality status: {report['status']}")
    print(f"Checks passed: {report.get('checks_passed', 0)}")
    print(f"Checks failed: {report.get('checks_failed', 0)}")
    if report.get("metrics", {}).get("demand_coverage"):
        coverage = report["metrics"]["demand_coverage"]
        print(f"Demand coverage: {coverage['start_date']} to {coverage['end_date']} "
              f"({coverage['distinct_dates']} dates, {coverage['rows']} rows)")
    print(f"JSON report: {report_path.resolve()}")
    if report["errors"]:
        print("\nIssues:")
        for error in report["errors"]:
            print(f" - {error}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
