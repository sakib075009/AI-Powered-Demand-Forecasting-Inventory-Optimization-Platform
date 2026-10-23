"""Baseline demand forecasting and chronological holdout backtesting for Project 02.

Forecast target: latent demand (demand_history.units_demanded), not fulfilled sales.
Writes forecast runs, per-day forecasts, and per-product/location metrics to the
existing SQLite schema. It never deletes or updates prior runs.

Usage:
    python -m src.run_forecasting_backtest --test-days 28
    python -m src.run_forecasting_backtest --db database/project02.db --test-days 28
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

MODELS = ("naive_last_value", "seasonal_naive_7", "moving_average_7")
REQUIRED_TABLES = {
    "demand_history", "products", "locations", "forecast_runs",
    "forecast_results", "forecast_evaluations",
}


def _canonical_date(value: str, label: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a valid YYYY-MM-DD date.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use canonical YYYY-MM-DD format.")
    return parsed


def _finite_nonnegative(value: float, label: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and nonnegative.")
    return value


def _predict(model: str, train: list[tuple[str, int]], target_dates: list[str]) -> list[float]:
    """Create a fixed-origin multi-step forecast using training data only."""
    values = [float(v) for _, v in train]
    if not values:
        raise ValueError("At least one training observation is required.")
    if model == "naive_last_value":
        predictions = [values[-1]] * len(target_dates)
    elif model == "moving_average_7":
        if len(values) < 7:
            raise ValueError("moving_average_7 requires at least 7 training observations.")
        average = statistics.fmean(values[-7:])
        predictions = [average] * len(target_dates)
    elif model == "seasonal_naive_7":
        if len(values) < 7:
            raise ValueError("seasonal_naive_7 requires at least 7 training observations.")
        last_train_date = _canonical_date(train[-1][0], "training date")
        # Repeat the final observed seven-day pattern, aligned by target offset.
        pattern = values[-7:]
        predictions = []
        for target in target_dates:
            target_day = _canonical_date(target, "target date")
            offset = (target_day - last_train_date).days
            predictions.append(pattern[(offset - 1) % 7])
    else:
        raise ValueError(f"Unsupported model: {model}")
    return [_finite_nonnegative(x, "forecast quantity") for x in predictions]


def _metrics(actual: list[float], forecast: list[float]) -> dict[str, float | None]:
    if not actual or len(actual) != len(forecast):
        raise ValueError("Actual and forecast arrays must be non-empty and equal length.")
    errors = [f - a for a, f in zip(actual, forecast)]
    absolute_errors = [abs(e) for e in errors]
    mae = statistics.fmean(absolute_errors)
    rmse = math.sqrt(statistics.fmean(e * e for e in errors))
    denominator = sum(actual)
    wape = sum(absolute_errors) / denominator if denominator > 0 else None
    bias = statistics.fmean(errors)  # positive = overforecast; negative = underforecast
    values = {"mae": mae, "rmse": rmse, "wape": wape, "forecast_bias": bias}
    for name, value in values.items():
        if value is not None and not math.isfinite(value):
            raise ValueError(f"Metric {name} is not finite.")
    if mae < 0 or rmse < 0 or (wape is not None and wape < 0):
        raise ValueError("Error metrics cannot be negative.")
    return values


def _check_schema(connection: sqlite3.Connection) -> None:
    existing = {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    missing = sorted(REQUIRED_TABLES - existing)
    if missing:
        raise RuntimeError("Database schema is missing required tables: " + ", ".join(missing))


def run_backtest(
    db_path: str | Path = "database/project02.db",
    *,
    test_days: int = 28,
    min_training_days: int = 56,
    models: tuple[str, ...] = MODELS,
    report_path: str | Path | None = "data/processed/forecast_backtest_report.json",
) -> dict[str, Any]:
    """Backtest models on the final test_days, using no test observations for fitting."""
    if test_days < 1:
        raise ValueError("test_days must be at least 1.")
    if min_training_days < 7:
        raise ValueError("min_training_days must be at least 7.")
    if not models or len(set(models)) != len(models):
        raise ValueError("models must be a non-empty tuple with no duplicates.")
    unsupported = sorted(set(models) - set(MODELS))
    if unsupported:
        raise ValueError(f"Unsupported models: {unsupported}")

    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise RuntimeError("Could not enable SQLite foreign-key enforcement.")

    try:
        _check_schema(connection)
        raw = connection.execute(
            "SELECT product_id, location_id, demand_date, units_demanded "
            "FROM demand_history ORDER BY product_id, location_id, demand_date"
        ).fetchall()
        if not raw:
            raise RuntimeError("demand_history is empty; generate operational data first.")
        groups: dict[tuple[int, int], list[tuple[str, int]]] = {}
        for row in raw:
            d = row["demand_date"]
            _canonical_date(d, "demand_history.demand_date")
            qty = row["units_demanded"]
            if not isinstance(qty, int) or qty < 0:
                raise ValueError(f"Invalid units_demanded for product/location/date {row['product_id']}/{row['location_id']}/{d}.")
            groups.setdefault((int(row["product_id"]), int(row["location_id"])), []).append((d, qty))

        # Validate a complete daily series per pair. Missing days are not silently filled with zero.
        pair_dates: list[str] | None = None
        for pair, series in groups.items():
            dates = [d for d, _ in series]
            if dates != sorted(set(dates)):
                raise RuntimeError(f"Duplicate or unsorted demand dates for pair {pair}.")
            if pair_dates is None:
                pair_dates = dates
            elif dates != pair_dates:
                raise RuntimeError(f"Product-location pair {pair} has incomplete or inconsistent date coverage.")
        all_dates = pair_dates or []
        if len(all_dates) <= min_training_days:
            raise RuntimeError(
                f"Need more than {min_training_days} daily observations per pair; found {len(all_dates)}."
            )
        if test_days >= len(all_dates):
            raise ValueError("test_days must be smaller than the available date history.")
        train_end_index = len(all_dates) - test_days
        train_dates = all_dates[:train_end_index]
        test_dates = all_dates[train_end_index:]
        if len(train_dates) < min_training_days:
            raise RuntimeError(
                f"Only {len(train_dates)} training days remain; need at least {min_training_days}."
            )

        run_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
        config = {
            "method": "chronological_fixed_origin_holdout",
            "target": "units_demanded",
            "test_days": test_days,
            "min_training_days": min_training_days,
            "training_observations": len(train_dates),
            "evaluation_observations": len(test_dates),
            "models": list(models),
            "metric_definitions": {
                "wape": "sum(abs(forecast-actual))/sum(actual); null when sum(actual)=0",
                "forecast_bias": "mean(forecast-actual); positive means overforecast",
            },
        }
        results_summary: list[dict[str, Any]] = []
        connection.execute("BEGIN IMMEDIATE")
        try:
            for model in models:
                cursor = connection.execute(
                    """INSERT INTO forecast_runs
                    (run_time, model_name, model_version, training_start_date, training_end_date,
                     forecast_start_date, forecast_end_date, evaluation_config, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')""",
                    (run_time, model, "1.0.0", train_dates[0], train_dates[-1],
                     test_dates[0], test_dates[-1], json.dumps(config, sort_keys=True)),
                )
                run_id = int(cursor.lastrowid)
                all_actual: list[float] = []
                all_forecast: list[float] = []
                pair_metrics: list[dict[str, Any]] = []
                for (product_id, location_id), series in sorted(groups.items()):
                    train = series[:train_end_index]
                    actual_rows = series[train_end_index:]
                    actual = [float(qty) for _, qty in actual_rows]
                    forecast = _predict(model, train, test_dates)
                    connection.executemany(
                        """INSERT INTO forecast_results
                        (forecast_run_id, product_id, location_id, target_date, forecast_qty)
                        VALUES (?, ?, ?, ?, ?)""",
                        [(run_id, product_id, location_id, target_date, qty)
                         for (target_date, _), qty in zip(actual_rows, forecast)],
                    )
                    metric = _metrics(actual, forecast)
                    connection.execute(
                        """INSERT INTO forecast_evaluations
                        (forecast_run_id, product_id, location_id, evaluation_start_date,
                         evaluation_end_date, horizon_days, mae, rmse, wape, forecast_bias)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (run_id, product_id, location_id, test_dates[0], test_dates[-1],
                         len(test_dates), metric["mae"], metric["rmse"], metric["wape"],
                         metric["forecast_bias"]),
                    )
                    pair_metrics.append({"product_id": product_id, "location_id": location_id, **metric})
                    all_actual.extend(actual)
                    all_forecast.extend(forecast)
                overall = _metrics(all_actual, all_forecast)
                connection.execute(
                    "UPDATE forecast_runs SET status='completed' WHERE forecast_run_id=?", (run_id,)
                )
                results_summary.append({
                    "forecast_run_id": run_id, "model_name": model,
                    "training_start_date": train_dates[0], "training_end_date": train_dates[-1],
                    "evaluation_start_date": test_dates[0], "evaluation_end_date": test_dates[-1],
                    "training_days": len(train_dates), "test_days": len(test_dates),
                    "series_count": len(groups), "forecast_rows": len(groups) * len(test_dates),
                    "evaluation_rows": len(pair_metrics), "overall_metrics": overall,
                    "per_series_metrics": pair_metrics,
                })
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        report = {
            "status": "PASS", "run_time_utc": run_time,
            "database": str(db_path), "target": "units_demanded",
            "method": "chronological_fixed_origin_holdout",
            "training_start_date": train_dates[0], "training_end_date": train_dates[-1],
            "evaluation_start_date": test_dates[0], "evaluation_end_date": test_dates[-1],
            "series_count": len(groups), "models": results_summary,
            "notes": [
                "All forecasts are generated using training-period observations only.",
                "Forecast target is latent demand (units_demanded), not sales constrained by inventory.",
                "WAPE is null when the aggregate actual-demand denominator is zero.",
                "Forecast bias is mean(forecast - actual); positive values indicate overforecasting.",
            ],
        }
        if report_path is not None:
            report_path = Path(report_path)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            temp = report_path.with_suffix(report_path.suffix + ".tmp")
            temp.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
            temp.replace(report_path)
        return report
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="database/project02.db", help="SQLite database path")
    parser.add_argument("--test-days", type=int, default=28, help="Number of final dates held out for evaluation")
    parser.add_argument("--min-training-days", type=int, default=56, help="Minimum training days per product-location series")
    parser.add_argument("--report", default="data/processed/forecast_backtest_report.json", help="JSON report path")
    args = parser.parse_args()
    try:
        report = run_backtest(args.db, test_days=args.test_days,
                              min_training_days=args.min_training_days, report_path=args.report)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Forecast backtest failed: {exc}", file=sys.stderr)
        return 1
    print("Forecast backtest status: PASS")
    print(f"Training: {report['training_start_date']} to {report['training_end_date']} ({report['models'][0]['training_days']} days)")
    print(f"Evaluation: {report['evaluation_start_date']} to {report['evaluation_end_date']} ({report['models'][0]['test_days']} days)")
    print(f"Product-location series: {report['series_count']}")
    for result in report["models"]:
        metrics = result["overall_metrics"]
        wape = "NULL" if metrics["wape"] is None else f"{metrics['wape']:.4f} ({metrics['wape']:.1%})"
        print(f"  {result['model_name']}: MAE={metrics['mae']:.4f}; RMSE={metrics['rmse']:.4f}; WAPE={wape}; Bias={metrics['forecast_bias']:.4f}; run_id={result['forecast_run_id']}")
    print(f"JSON report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
