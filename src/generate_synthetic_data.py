"""Reproducible synthetic supply-chain data generator for Project 02.

Populates the nine operational/master tables in an EMPTY initialized SQLite
database. Forecasting and planning tables remain empty until their own phases.

Usage:
    python -m src.generate_synthetic_data --seed 20261023 --history-days 180 --end-date 2026-10-23
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "0.1.0"

MASTER_TABLES = (
    "products", "locations", "suppliers", "supplier_products",
)
OPERATIONAL_TABLES = (
    "demand_history", "inventory_snapshots", "inventory_movements",
    "purchase_orders", "purchase_order_lines",
)
FUTURE_TABLES = (
    "forecast_runs", "forecast_results", "forecast_evaluations",
    "planning_runs", "replenishment_recommendations",
)
ALL_TABLES = MASTER_TABLES + OPERATIONAL_TABLES + FUTURE_TABLES

PRODUCT_CATALOG = [
    ("Organic Rice 1kg", "Staples", "pack"),
    ("Red Lentils 1kg", "Staples", "pack"),
    ("Mustard Oil 500ml", "Cooking Oil", "bottle"),
    ("Sunflower Oil 1L", "Cooking Oil", "bottle"),
    ("Black Tea 200g", "Beverages", "pack"),
    ("Ground Coffee 100g", "Beverages", "pack"),
    ("Glass Storage Jar", "Kitchenware", "piece"),
    ("Steel Jar Cover", "Kitchenware", "piece"),
    ("Cardboard Shipping Box", "Packaging", "piece"),
    ("Protective Packaging Roll", "Packaging", "roll"),
    ("Organic Chickpeas 500g", "Staples", "pack"),
    ("Spice Mix 100g", "Spices", "pack"),
]
LOCATION_CATALOG = [
    ("DHK-01", "Dhaka Central", "warehouse", "Dhaka"),
    ("CTG-01", "Chattogram Hub", "warehouse", "Chattogram"),
    ("RAJ-01", "Rajshahi Distribution", "distribution_center", "Rajshahi"),
]
SUPPLIER_CATALOG = [
    ("SUP-001", "Delta Wholesale"),
    ("SUP-002", "Green Source Trading"),
    ("SUP-003", "Eastern Supply House"),
    ("SUP-004", "Reliable Goods Ltd"),
    ("SUP-005", "National Distribution Co"),
]


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler; lambdas in this simulator are deliberately small."""
    if lam <= 0:
        return 0
    limit = math.exp(-lam)
    product = 1.0
    count = 0
    while product > limit:
        count += 1
        product *= rng.random()
    return count - 1


def _iso(value: date) -> str:
    return value.isoformat()


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _require_empty_database(connection: sqlite3.Connection) -> None:
    missing = [
        table for table in ALL_TABLES
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is None
    ]
    if missing:
        raise RuntimeError(
            "Database schema is incomplete; missing tables: " + ", ".join(missing)
        )
    populated = {table: _table_count(connection, table) for table in ALL_TABLES}
    populated = {table: count for table, count in populated.items() if count}
    if populated:
        details = ", ".join(f"{table}={count}" for table, count in sorted(populated.items()))
        raise RuntimeError(
            "Refusing to append or overwrite data. The generator requires an EMPTY "
            f"initialized database; existing rows: {details}. Use a separate fresh "
            "database for a new generation run."
        )


def generate_dataset(
    db_path: str | Path,
    *,
    seed: int = 20261023,
    history_days: int = 180,
    end_date: str | date = "2026-10-23",
    metadata_path: str | Path | None = None,
) -> dict[str, Any]:
    """Populate a fresh initialized database and return generation summary.

    Reproducibility requires the same seed, history_days, end_date, and generator
    version. Existing business data is never deleted or overwritten.
    """
    if history_days < 30:
        raise ValueError("history_days must be at least 30 for a useful demand history.")
    if isinstance(end_date, str):
        try:
            end_day = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("end_date must use YYYY-MM-DD format.") from exc
        if end_day.isoformat() != end_date:
            raise ValueError("end_date must use YYYY-MM-DD format.")
    else:
        end_day = end_date
    if metadata_path is None:
        metadata_path = Path("data/processed/dataset_metadata.json")
    metadata_path = Path(metadata_path)
    db_path = Path(db_path)

    rng = random.Random(seed)
    start_day = end_day - timedelta(days=history_days - 1)
    terms_start = start_day - timedelta(days=365)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")

    try:
        _require_empty_database(connection)
        connection.execute("BEGIN IMMEDIATE")

        # 1) Master data.
        product_ids: list[int] = []
        product_info: dict[int, dict[str, Any]] = {}
        for index, (name, category, uom) in enumerate(PRODUCT_CATALOG, start=1):
            unit_cost = rng.randint(8000, 95000)  # BDT poisha
            selling_price = unit_cost + rng.randint(max(1000, unit_cost // 5), unit_cost)
            cursor = connection.execute(
                """
                INSERT INTO products
                    (sku, product_name, category, unit_cost, selling_price,
                     unit_of_measure, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (f"SKU-{index:04d}", name, category, unit_cost, selling_price, uom),
            )
            product_id = int(cursor.lastrowid)
            product_ids.append(product_id)
            product_info[product_id] = {
                "name": name, "category": category, "unit_cost": unit_cost,
                "selling_price": selling_price,
            }

        location_ids: list[int] = []
        location_codes: dict[int, str] = {}
        for code, name, kind, region in LOCATION_CATALOG:
            cursor = connection.execute(
                """
                INSERT INTO locations
                    (location_code, location_name, location_type, region, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (code, name, kind, region),
            )
            location_id = int(cursor.lastrowid)
            location_ids.append(location_id)
            location_codes[location_id] = code

        supplier_ids: list[int] = []
        for code, name in SUPPLIER_CATALOG:
            cursor = connection.execute(
                """
                INSERT INTO suppliers
                    (supplier_code, supplier_name, contact_reference,
                     default_lead_time_days, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (code, name, f"synthetic-contact-{code.lower()}", rng.randint(3, 10)),
            )
            supplier_ids.append(int(cursor.lastrowid))

        # Two supplier options per product; one preferred option per product.
        supplier_terms: dict[int, list[dict[str, int]]] = defaultdict(list)
        for product_id in product_ids:
            chosen = rng.sample(supplier_ids, 2)
            costs = [
                max(1000, int(product_info[product_id]["unit_cost"] * rng.uniform(0.88, 1.08)))
                for _ in chosen
            ]
            preferred_index = costs.index(min(costs))
            for idx, supplier_id in enumerate(chosen):
                lead_time = rng.randint(3, 10)
                min_order = rng.choice([5, 10, 12, 20])
                multiple = rng.choice([1, 5, 10])
                cursor = connection.execute(
                    """
                    INSERT INTO supplier_products
                        (supplier_id, product_id, unit_purchase_cost, lead_time_days,
                         min_order_qty, order_multiple, is_preferred,
                         effective_from, effective_to)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        supplier_id, product_id, costs[idx], lead_time, min_order,
                        multiple, 1 if idx == preferred_index else 0, _iso(terms_start),
                    ),
                )
                supplier_terms[product_id].append({
                    "supplier_id": supplier_id,
                    "unit_purchase_cost": costs[idx],
                    "lead_time_days": lead_time,
                    "min_order_qty": min_order,
                    "order_multiple": multiple,
                    "supplier_product_id": int(cursor.lastrowid),
                    "is_preferred": 1 if idx == preferred_index else 0,
                })

        # 2) Daily demand, stock, receipts and purchase orders.
        # Purchase orders have no location_id in the approved schema. For this
        # synthetic dataset only, external_reference carries the allocation:
        # SIM-{location_code}-P{product_id}-{sequence}. This convention is recorded
        # in metadata so the simulator's allocation can be interpreted.
        demand_rows = 0
        snapshot_rows = 0
        movement_rows = 0
        purchase_order_rows = 0
        purchase_order_line_rows = 0
        stockout_rows = 0
        pending_by_pair: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        next_order_seq: dict[tuple[int, int], int] = defaultdict(int)
        on_hand_by_pair: dict[tuple[int, int], int] = {}
        avg_demand_by_pair: dict[tuple[int, int], float] = {}
        reorder_point_by_pair: dict[tuple[int, int], int] = {}
        chosen_term_by_pair: dict[tuple[int, int], dict[str, int]] = {}

        for product_id in product_ids:
            for location_id in location_ids:
                pair = (product_id, location_id)
                term = next(t for t in supplier_terms[product_id] if t["is_preferred"])
                chosen_term_by_pair[pair] = term
                avg_demand = rng.uniform(3.0, 7.5)
                avg_demand_by_pair[pair] = avg_demand
                reorder_point_by_pair[pair] = max(
                    term["min_order_qty"],
                    math.ceil(avg_demand * term["lead_time_days"] + avg_demand * 2.0),
                )
                on_hand_by_pair[pair] = rng.randint(25, 70)

        for offset in range(history_days):
            current_day = start_day + timedelta(days=offset)
            weekday_factor = 1.18 if current_day.weekday() in (4, 5) else 0.92 if current_day.weekday() == 0 else 1.0

            for product_id in product_ids:
                product = product_info[product_id]
                for location_id in location_ids:
                    pair = (product_id, location_id)
                    term = chosen_term_by_pair[pair]
                    avg_demand = avg_demand_by_pair[pair]
                    pending = pending_by_pair[pair]

                    # Receive any purchase orders scheduled for this date.
                    for order in list(pending):
                        if order["expected_day"] == current_day:
                            connection.execute(
                                """
                                UPDATE purchase_orders
                                SET status='received', actual_delivery_date=?
                                WHERE purchase_order_id=?
                                """,
                                (_iso(current_day), order["purchase_order_id"]),
                            )
                            connection.execute(
                                """
                                UPDATE purchase_order_lines
                                SET received_qty=ordered_qty
                                WHERE purchase_order_id=?
                                """,
                                (order["purchase_order_id"],),
                            )
                            on_hand_by_pair[pair] += order["quantity"]
                            connection.execute(
                                """
                                INSERT INTO inventory_movements
                                    (product_id, location_id, movement_time, movement_type,
                                     quantity_delta, reference_type, reference_id, notes)
                                VALUES (?, ?, ?, 'receipt', ?, 'purchase_order', ?, ?)
                                """,
                                (
                                    product_id, location_id, f"{_iso(current_day)}T08:00:00",
                                    order["quantity"], str(order["purchase_order_id"]),
                                    "Synthetic supplier receipt",
                                ),
                            )
                            movement_rows += 1
                            pending.remove(order)

                    # Demand is latent demand; sales are capped by available on-hand stock.
                    month_factor = 1.12 if current_day.month in (11, 12) else 1.0
                    lam = avg_demand * weekday_factor * month_factor
                    units_demanded = _poisson(rng, lam)
                    units_sold = min(units_demanded, on_hand_by_pair[pair])
                    lost = units_demanded - units_sold
                    on_hand_by_pair[pair] -= units_sold
                    stockout = int(lost > 0)
                    stockout_rows += stockout
                    sales_revenue = units_sold * int(product["selling_price"])

                    cursor = connection.execute(
                        """
                        INSERT INTO demand_history
                            (product_id, location_id, demand_date, units_demanded,
                             units_sold, stockout_flag, estimated_lost_demand, sales_revenue)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            product_id, location_id, _iso(current_day), units_demanded,
                            units_sold, stockout, lost, sales_revenue,
                        ),
                    )
                    demand_id = int(cursor.lastrowid)
                    demand_rows += 1
                    if units_sold > 0:
                        connection.execute(
                            """
                            INSERT INTO inventory_movements
                                (product_id, location_id, movement_time, movement_type,
                                 quantity_delta, reference_type, reference_id, notes)
                            VALUES (?, ?, ?, 'sale', ?, 'demand_history', ?, ?)
                            """,
                            (
                                product_id, location_id, f"{_iso(current_day)}T18:00:00",
                                -units_sold, str(demand_id), "Synthetic fulfilled sales",
                            ),
                        )
                        movement_rows += 1

                    # Reorder only when no outstanding PO exists for this product-location.
                    on_order = sum(order["quantity"] for order in pending)
                    inventory_position = on_hand_by_pair[pair] + on_order
                    reorder_point = reorder_point_by_pair[pair]
                    if inventory_position <= reorder_point and not pending:
                        next_order_seq[pair] += 1
                        target_stock = reorder_point + math.ceil(avg_demand * 14)
                        needed = max(term["min_order_qty"], target_stock - inventory_position)
                        multiple = term["order_multiple"]
                        order_qty = int(math.ceil(needed / multiple) * multiple)
                        lead_time = term["lead_time_days"]
                        expected_day = current_day + timedelta(days=lead_time)
                        external_ref = (
                            f"SIM-{location_codes[location_id]}-P{product_id:04d}-"
                            f"{next_order_seq[pair]:04d}"
                        )
                        cursor = connection.execute(
                            """
                            INSERT INTO purchase_orders
                                (supplier_id, order_date, expected_delivery_date,
                                 actual_delivery_date, status, external_reference)
                            VALUES (?, ?, ?, NULL, 'ordered', ?)
                            """,
                            (term["supplier_id"], _iso(current_day), _iso(expected_day), external_ref),
                        )
                        purchase_order_id = int(cursor.lastrowid)
                        connection.execute(
                            """
                            INSERT INTO purchase_order_lines
                                (purchase_order_id, product_id, ordered_qty, received_qty,
                                 cancelled_qty, unit_cost)
                            VALUES (?, ?, ?, 0, 0, ?)
                            """,
                            (
                                purchase_order_id, product_id, order_qty,
                                term["unit_purchase_cost"],
                            ),
                        )
                        purchase_order_rows += 1
                        purchase_order_line_rows += 1
                        pending.append({
                            "purchase_order_id": purchase_order_id,
                            "quantity": order_qty,
                            "expected_day": expected_day,
                        })
                        on_order = order_qty

                    connection.execute(
                        """
                        INSERT INTO inventory_snapshots
                            (product_id, location_id, snapshot_time, on_hand_qty,
                             reserved_qty, on_order_qty, backorder_qty)
                        VALUES (?, ?, ?, ?, 0, ?, 0)
                        """,
                        (
                            product_id, location_id, f"{_iso(current_day)}T23:59:59",
                            on_hand_by_pair[pair], on_order,
                        ),
                    )
                    snapshot_rows += 1

        # The five forecast/planning tables intentionally remain empty at this phase.
        for table in FUTURE_TABLES:
            if _table_count(connection, table) != 0:
                raise RuntimeError(f"Unexpected rows in future-phase table {table}.")

        connection.commit()
        counts = {table: _table_count(connection, table) for table in ALL_TABLES}
        fk_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            raise RuntimeError(f"Foreign-key violations after generation: {len(fk_violations)}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    metadata = {
        "dataset_name": "project02_synthetic_supply_chain",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "history_days": history_days,
        "start_date": _iso(start_day),
        "end_date": _iso(end_day),
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "currency": "BDT",
        "currency_storage": "integer poisha; 100 poisha = BDT 1",
        "synthetic_data": True,
        "tables_populated": list(MASTER_TABLES + OPERATIONAL_TABLES),
        "future_phase_tables_left_empty": list(FUTURE_TABLES),
        "purchase_order_location_convention": (
            "The approved purchase_orders schema has no location_id. In this synthetic "
            "dataset only, location allocation is encoded in external_reference as "
            "SIM-{location_code}-P{product_id}-{sequence}; use this convention only to "
            "interpret generated demo records, not as a normalized production design."
        ),
        "row_counts": counts,
        "metrics": {
            "demand_rows": demand_rows,
            "inventory_snapshot_rows": snapshot_rows,
            "inventory_movement_rows": movement_rows,
            "purchase_order_rows": purchase_order_rows,
            "purchase_order_line_rows": purchase_order_line_rows,
            "stockout_days": stockout_rows,
        },
    }
    _json_dump(metadata_path, metadata)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reproducible synthetic Project 02 data.")
    parser.add_argument("--db", default="database/project02.db", help="Initialized SQLite database path.")
    parser.add_argument("--seed", type=int, default=20261023, help="Random seed.")
    parser.add_argument("--history-days", type=int, default=180, help="Number of daily history dates (minimum 30).")
    parser.add_argument("--end-date", default="2026-10-23", help="Inclusive history end date (YYYY-MM-DD).")
    parser.add_argument(
        "--metadata", default="data/processed/dataset_metadata.json",
        help="Output path for dataset provenance JSON.",
    )
    args = parser.parse_args()
    summary = generate_dataset(
        args.db, seed=args.seed, history_days=args.history_days,
        end_date=args.end_date, metadata_path=args.metadata,
    )
    print("Synthetic dataset generated successfully.")
    print(f"Database: {Path(args.db).resolve()}")
    print(f"History: {summary['start_date']} to {summary['end_date']} ({summary['history_days']} days)")
    print(f"Seed: {summary['seed']}")
    print("\nRow counts:")
    for table, count in summary["row_counts"].items():
        print(f"  {table:<34} {count:>8}")
    print(f"\nStockout days: {summary['metrics']['stockout_days']}")
    print(f"Metadata: {Path(args.metadata).resolve()}")
    print("No Git commit was created.")


if __name__ == "__main__":
    main()
