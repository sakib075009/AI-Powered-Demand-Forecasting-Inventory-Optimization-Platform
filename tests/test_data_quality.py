"""Tests for the Block 3 synthetic-data quality audit."""
import sqlite3

import pytest

from src.db_init import initialize_database
from src.generate_synthetic_data import generate_dataset
from src.validate_synthetic_data import validate_synthetic_data


def _fresh_generated_db(tmp_path, days=30):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "project02.db"
    initialize_database(db_path)
    generate_dataset(
        db_path,
        seed=20261023,
        history_days=days,
        end_date="2026-10-23",
        metadata_path=tmp_path / "metadata.json",
    )
    return db_path


def _connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def test_clean_synthetic_dataset_passes_all_quality_checks(tmp_path):
    db_path = _fresh_generated_db(tmp_path)
    connection = _connect(db_path)
    try:
        report = validate_synthetic_data(
            connection,
            expected_start_date="2026-09-24",
            expected_end_date="2026-10-23",
        )
        assert report["status"] == "PASS", report["errors"]
        assert report["checks_failed"] == 0
        assert report["metrics"]["demand_coverage"]["rows"] == 12 * 3 * 30
    finally:
        connection.close()


def test_validator_detects_demand_business_rule_corruption(tmp_path):
    db_path = _fresh_generated_db(tmp_path)
    connection = _connect(db_path)
    try:
        connection.execute("UPDATE demand_history SET estimated_lost_demand=999 WHERE demand_id=(SELECT MIN(demand_id) FROM demand_history)")
        connection.commit()
        report = validate_synthetic_data(connection)
        assert report["status"] == "FAIL"
        assert any("demand_business_rules" in error for error in report["errors"])
    finally:
        connection.close()


def test_validator_detects_missing_demand_date(tmp_path):
    db_path = _fresh_generated_db(tmp_path)
    connection = _connect(db_path)
    try:
        connection.execute("DELETE FROM demand_history WHERE demand_id=(SELECT MIN(demand_id) FROM demand_history)")
        connection.commit()
        report = validate_synthetic_data(connection)
        assert report["status"] == "FAIL"
        assert any("complete_daily_demand_coverage" in error for error in report["errors"])
        assert any("demand_snapshot_date_alignment" in error for error in report["errors"])
    finally:
        connection.close()


def test_expected_date_arguments_are_enforced(tmp_path):
    db_path = _fresh_generated_db(tmp_path)
    connection = _connect(db_path)
    try:
        report = validate_synthetic_data(
            connection,
            expected_start_date="2024-01-01",
            expected_end_date="2025-12-31",
        )
        assert report["status"] == "FAIL"
        assert any("expected_start_date" in error for error in report["errors"])
        assert any("expected_end_date" in error for error in report["errors"])
    finally:
        connection.close()


def test_validator_does_not_modify_database(tmp_path):
    db_path = _fresh_generated_db(tmp_path)
    connection = _connect(db_path)
    try:
        before = connection.execute("SELECT COUNT(*) FROM demand_history").fetchone()[0]
        validate_synthetic_data(connection)
        after = connection.execute("SELECT COUNT(*) FROM demand_history").fetchone()[0]
        assert before == after
    finally:
        connection.close()
