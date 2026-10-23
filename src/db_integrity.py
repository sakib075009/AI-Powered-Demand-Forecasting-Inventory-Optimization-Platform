"""Application-level integrity checks for Project 02.

These checks complement SQLite constraints for cross-row and date rules that
are not fully enforced by the baseline schema.
"""
from __future__ import annotations

from datetime import date
import math
import sqlite3
from typing import Any


POSITIVE_MOVEMENTS = {
    "receipt", "customer_return", "adjustment_increase", "transfer_in"
}
NEGATIVE_MOVEMENTS = {
    "sale", "supplier_return", "adjustment_decrease", "transfer_out",
    "damage_or_writeoff",
}


def require_finite(value: Any, field_name: str) -> float:
    """Return a finite float or raise ValueError before persisting a REAL value."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number.")
    return number


def _parse_iso_date(value: str | None, field_name: str, errors: list[str]) -> date | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        errors.append(f"{field_name} must be a valid ISO date; got {value!r}.")
        return None
    # date.fromisoformat accepts compact forms; require canonical YYYY-MM-DD.
    if parsed.isoformat() != value:
        errors.append(f"{field_name} must use YYYY-MM-DD format; got {value!r}.")
        return None
    return parsed


def validate_supplier_product_periods(connection: sqlite3.Connection) -> list[str]:
    """Validate ISO dates and non-overlapping inclusive periods per supplier/product."""
    errors: list[str] = []
    rows = connection.execute(
        """
        SELECT supplier_product_id, supplier_id, product_id, effective_from, effective_to
        FROM supplier_products
        ORDER BY supplier_id, product_id, effective_from, supplier_product_id
        """
    ).fetchall()

    previous_by_pair: dict[tuple[int, int], tuple[int, date, date | None]] = {}
    for row in rows:
        row_id = row["supplier_product_id"]
        start = _parse_iso_date(
            row["effective_from"], f"supplier_products[{row_id}].effective_from", errors
        )
        end = _parse_iso_date(
            row["effective_to"], f"supplier_products[{row_id}].effective_to", errors
        )
        if start is None:
            continue
        if row["effective_to"] is not None and end is None:
            continue
        if end is not None and end < start:
            errors.append(
                f"supplier_products[{row_id}] effective_to precedes effective_from."
            )
            continue

        pair = (row["supplier_id"], row["product_id"])
        previous = previous_by_pair.get(pair)
        if previous is not None:
            previous_id, previous_start, previous_end = previous
            if previous_end is None or start <= previous_end:
                errors.append(
                    f"supplier_products[{row_id}] overlaps inclusive effective period "
                    f"for supplier-product row {previous_id}."
                )
            # Keep the furthest-ending period so nested/overlapping records are detected.
            if previous_end is None or (end is not None and end <= previous_end):
                continue
        previous_by_pair[pair] = (row_id, start, end)
    return errors


def validate_purchase_order_reconciliation(connection: sqlite3.Connection) -> list[str]:
    """Validate purchase-order header status against all its line quantities."""
    errors: list[str] = []
    orders = connection.execute(
        "SELECT purchase_order_id, status FROM purchase_orders ORDER BY purchase_order_id"
    ).fetchall()

    for order in orders:
        order_id = order["purchase_order_id"]
        status = order["status"]
        lines = connection.execute(
            """
            SELECT purchase_order_line_id, ordered_qty, received_qty, cancelled_qty
            FROM purchase_order_lines
            WHERE purchase_order_id = ?
            ORDER BY purchase_order_line_id
            """,
            (order_id,),
        ).fetchall()

        if not lines:
            errors.append(f"Purchase order {order_id} has no lines.")
            continue

        for line in lines:
            line_id = line["purchase_order_line_id"]
            ordered = line["ordered_qty"]
            received = line["received_qty"]
            cancelled = line["cancelled_qty"]
            if received < 0 or cancelled < 0 or received + cancelled > ordered:
                errors.append(f"Purchase-order line {line_id} has invalid quantity reconciliation.")
            if status in {"draft", "approved", "ordered", "partially_received"} and cancelled != 0:
                errors.append(
                    f"Purchase-order line {line_id} has cancelled quantity while order "
                    f"{order_id} is {status}."
                )
            if status == "ordered" and received != 0:
                errors.append(
                    f"Purchase order {order_id} is ordered but line {line_id} has received quantity."
                )
            if status == "received" and (received != ordered or cancelled != 0):
                errors.append(
                    f"Received purchase order {order_id} line {line_id} must be fully received "
                    "with zero cancelled quantity."
                )
            if status == "cancelled" and received + cancelled != ordered:
                errors.append(
                    f"Cancelled purchase order {order_id} line {line_id} must reconcile "
                    "received_qty + cancelled_qty to ordered_qty."
                )

        if status == "partially_received":
            total_received = sum(line["received_qty"] for line in lines)
            total_outstanding = sum(
                line["ordered_qty"] - line["received_qty"] - line["cancelled_qty"]
                for line in lines
            )
            if total_received <= 0 or total_outstanding <= 0:
                errors.append(
                    f"Partially received purchase order {order_id} must have both received "
                    "and outstanding quantities."
                )
    return errors


def validate_recommendation_supplier_match(connection: sqlite3.Connection) -> list[str]:
    """Ensure each recommendation uses matching, effective supplier terms on planning_date."""
    errors: list[str] = []
    rows = connection.execute(
        """
        SELECT r.recommendation_id, r.product_id, r.supplier_product_id,
               p.planning_date, sp.product_id AS supplier_product_product_id,
               sp.effective_from, sp.effective_to
        FROM replenishment_recommendations AS r
        JOIN planning_runs AS p ON p.planning_run_id = r.planning_run_id
        JOIN supplier_products AS sp ON sp.supplier_product_id = r.supplier_product_id
        """
    ).fetchall()

    for row in rows:
        rec_id = row["recommendation_id"]
        planning_date = _parse_iso_date(
            row["planning_date"], f"planning_runs for recommendation {rec_id}.planning_date", errors
        )
        start = _parse_iso_date(
            row["effective_from"], f"supplier_products for recommendation {rec_id}.effective_from", errors
        )
        end = _parse_iso_date(
            row["effective_to"], f"supplier_products for recommendation {rec_id}.effective_to", errors
        )
        if row["product_id"] != row["supplier_product_product_id"]:
            errors.append(
                f"Recommendation {rec_id} product does not match its supplier-product record."
            )
        if planning_date is None or start is None:
            continue
        if row["effective_to"] is not None and end is None:
            continue
        if planning_date < start or (end is not None and planning_date > end):
            errors.append(
                f"Recommendation {rec_id} uses supplier terms not effective on planning_date."
            )
    return errors


def validate_database_integrity(connection: sqlite3.Connection) -> list[str]:
    """Run all implemented cross-row integrity checks and return actionable errors."""
    errors: list[str] = []
    errors.extend(validate_supplier_product_periods(connection))
    errors.extend(validate_purchase_order_reconciliation(connection))
    errors.extend(validate_recommendation_supplier_match(connection))
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    for row in foreign_key_errors:
        errors.append(f"Foreign-key violation: {tuple(row)}")
    return errors
