from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

from src.db_init import initialize_database
from src.generate_synthetic_data import generate_dataset
from src.run_forecasting_backtest import _metrics, _predict, run_backtest


def test_metrics_known_values():
    result = _metrics([10, 20, 30], [12, 18, 33])
    assert result["mae"] == pytest.approx(7 / 3)
    assert result["rmse"] == pytest.approx(math.sqrt(17 / 3))
    assert result["wape"] == pytest.approx(7 / 60)
    assert result["forecast_bias"] == pytest.approx(1)


def test_wape_null_when_actual_sum_zero():
    result = _metrics([0, 0], [0, 2])
    assert result["wape"] is None


def test_predict_methods_nonnegative_and_fixed_origin():
    train = [(f"2026-01-{day:02d}", day) for day in range(1, 15)]
    targets = ["2026-01-15", "2026-01-16", "2026-01-17"]
    assert _predict("naive_last_value", train, targets) == [14.0] * 3
    assert _predict("moving_average_7", train, targets) == pytest.approx([11.0] * 3)
    assert _predict("seasonal_naive_7", train, targets) == [8.0, 9.0, 10.0]


def _fresh_database(tmp_path: Path) -> Path:
    db_path = tmp_path / "database" / "test.db"
    initialize_database(db_path)
    generate_dataset(db_path, seed=20261023, history_days=100,
                     end_date="2026-10-23", metadata_path=tmp_path / "meta.json")
    return db_path


def test_backtest_writes_all_three_models_and_reports(tmp_path):
    db_path = _fresh_database(tmp_path)
    report_path = tmp_path / "report" / "forecast.json"
    report = run_backtest(db_path, test_days=14, min_training_days=56, report_path=report_path)
    assert report["status"] == "PASS"
    assert report["series_count"] == 36
    assert len(report["models"]) == 3
    assert report_path.exists()
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["evaluation_start_date"] == "2026-10-10"
    assert saved["evaluation_end_date"] == "2026-10-23"
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("SELECT COUNT(*) FROM forecast_runs WHERE status='completed'").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM forecast_results").fetchone()[0] == 3 * 36 * 14
        assert con.execute("SELECT COUNT(*) FROM forecast_evaluations").fetchone()[0] == 3 * 36
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT COUNT(*) FROM forecast_results WHERE forecast_qty < 0").fetchone()[0] == 0
    finally:
        con.close()


def test_no_data_mutation_outside_forecast_tables(tmp_path):
    db_path = _fresh_database(tmp_path)
    con = sqlite3.connect(db_path)
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("demand_history", "inventory_snapshots", "inventory_movements", "purchase_orders")}
    con.close()
    run_backtest(db_path, test_days=14, min_training_days=56, report_path=None)
    con = sqlite3.connect(db_path)
    try:
        after = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in before}
        assert after == before
    finally:
        con.close()


def test_rejects_test_window_too_large(tmp_path):
    db_path = _fresh_database(tmp_path)
    with pytest.raises(ValueError, match="smaller than"):
        run_backtest(db_path, test_days=100, min_training_days=56, report_path=None)


def test_rejects_unknown_model(tmp_path):
    db_path = _fresh_database(tmp_path)
    with pytest.raises(ValueError, match="Unsupported models"):
        run_backtest(db_path, test_days=14, models=("magical_model",), report_path=None)


def test_invalid_metrics_are_rejected():
    with pytest.raises(ValueError, match="equal length"):
        _metrics([1, 2], [1])
    with pytest.raises(ValueError, match="non-empty"):
        _metrics([], [])
