from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from thermal_sentinel.storage import (
    AlertNotFoundError,
    AlertStateError,
    EquipmentExistsError,
    EquipmentNotFoundError,
    MonitoringStore,
)


def test_default_bays_bound_sensors_and_persistence(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "sentinel.db"
    with MonitoringStore(db_file) as store:
        assert store.ping() is True
        bays = store.list_equipment()
        assert [b.bay_id for b in bays] == ["B1", "B2", "B3", "B4", "B5", "B6"]
        assert [b.sensor_id for b in bays] == [f"thermocouple-B{i}" for i in range(1, 7)]
        assert all(b.status == "nominal" for b in bays)
        assert store.count_summary() == (6, 0)

        created = store.create_equipment("B7", "Bay 07", 11.0, 2.0, baseline_temp_c=30.0, sensor_id="tc-B7")
        assert created.bay_id == "B7"
        assert created.sensor_id == "tc-B7"
        assert created.baseline_temp_c == 30.0

    # Re-opening existing DB preserves B7 and does not overwrite existing rows
    with MonitoringStore(db_file) as reopened:
        assert reopened.count_summary() == (7, 0)
        assert reopened.get_equipment("B7").label == "Bay 07"
        assert reopened.get_equipment("B7").sensor_id == "tc-B7"

    # Closing connection causes ping() to return False cleanly rather than throwing
    assert reopened.ping() is False


def test_memory_store_uniqueness_and_validation_errors() -> None:
    with pytest.raises(ValueError, match="finite"):
        MonitoringStore(":memory:", default_baseline_c=math.nan)

    with MonitoringStore(":memory:", seed_defaults=False) as store:
        assert store.list_equipment() == []
        store.create_equipment("B1", "Bay 01", 2.0, 1.0, 28.0)

        with pytest.raises(EquipmentExistsError, match="already exists"):
            store.create_equipment("B1", "Bay 01", 2.0, 1.0, 28.0)

        with pytest.raises(EquipmentExistsError, match="already exists"):
            store.create_equipment("B2", "Bay 02", 5.0, 1.0, 28.0, sensor_id="thermocouple-B1")

        with pytest.raises(ValueError, match="finite"):
            store.create_equipment("B3", "Bay 03", math.inf, 1.0, 28.0)

        with pytest.raises(EquipmentNotFoundError, match="B99"):
            store.get_equipment("B99")

        with pytest.raises(AlertNotFoundError, match="999"):
            store.acknowledge_alert(999)

        with pytest.raises(ValueError, match="confirm=True"):
            store.reset_monitoring_state()

        with pytest.raises(ValueError, match="finite"):
            store.reset_monitoring_state(confirm=True, baseline_overrides={"B1": math.nan})


def test_sqlite_schema_constraints_reject_duplicates_and_bad_statuses() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        store.record_reading_and_state(
            bay_id="B1",
            sensor_id="thermocouple-B1",
            temperature_c=34.0,
            delta_c=6.0,
            recorded_at=t0,
            status="alert",
            ewma_delta_c=3.0,
            cusum_score=10.0,
            bayes_post=(0.1, 0.1, 0.8),
            bayes_mu_fault=3.0,
            alert_active=True,
            alert_armed=True,
            rearm_after=None,
            open_alert=("bayesian_hmm", "high", "Overheat", 0.8),
        )

        # Partial unique index forbids two active alerts on one bay
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                """
                INSERT INTO alerts (bay_id, detector, severity, status, message, metric_value, created_at)
                VALUES ('B1', 'ewma', 'high', 'open', 'Duplicate', 2.0, '2026-01-01T12:00:01+00:00')
                """
            )

        # CHECK constraint forbids invalid status
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute("UPDATE equipment SET status = 'broken' WHERE bay_id = 'B1'")


def test_alert_acknowledge_lifecycle_and_reset_confirm(tmp_path: Path) -> None:
    db_file = tmp_path / "state.db"
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with MonitoringStore(db_file) as store:
        reading, eq, alert = store.record_reading_and_state(
            bay_id="B2",
            sensor_id="thermocouple-B2",
            temperature_c=33.0,
            delta_c=5.0,
            recorded_at=t0,
            status="alert",
            ewma_delta_c=2.5,
            cusum_score=10.0,
            bayes_post=(0.1, 0.1, 0.8),
            bayes_mu_fault=3.0,
            alert_active=True,
            alert_armed=True,
            rearm_after=None,
            open_alert=("bayesian_hmm", "high", "Overheat", 0.8),
        )
        assert reading.bay_id == "B2"
        assert eq.alert_active is True
        assert alert is not None
        assert alert.status == "open"
        assert store.count_summary() == (6, 1)

        acked = store.acknowledge_alert(alert.id)
        assert acked.status == "acknowledged"
        assert store.acknowledge_alert(alert.id).status == "acknowledged"

        naive_t1 = datetime(2026, 1, 1, 12, 1, 0)
        store.record_reading_and_state(
            bay_id="B2",
            sensor_id="thermocouple-B2",
            temperature_c=28.0,
            delta_c=0.0,
            recorded_at=naive_t1,
            status="nominal",
            ewma_delta_c=0.0,
            cusum_score=0.0,
            bayes_post=(0.995, 0.004, 0.001),
            bayes_mu_fault=1.2,
            alert_active=False,
            alert_armed=True,
            rearm_after=None,
            resolve_alerts=True,
        )
        with pytest.raises(AlertStateError, match="resolved"):
            store.acknowledge_alert(alert.id)

        assert len(store.list_readings()) == 2
        assert len(store.list_readings(bay_id="B2", since=t0 + timedelta(seconds=30), limit=10)) == 1
        assert store.list_alerts(status="resolved", bay_id="B2")[0].resolved_at is not None

        store.reset_monitoring_state(confirm=True, baseline_overrides={"B2": 29.0})
        assert store.list_readings() == []
        assert store.list_alerts() == []
        assert store.get_equipment("B2").baseline_temp_c == 29.0

        store.reset_monitoring_state(confirm=True)
        assert store.get_equipment("B2").baseline_temp_c == 29.0


def test_update_baseline_resolves_open_and_acknowledged_alerts(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with MonitoringStore(tmp_path / "baseline_alerts.db") as store:
        for bay_id in ("B1", "B2", "B3"):
            store.record_reading_and_state(
                bay_id=bay_id,
                sensor_id=f"thermocouple-{bay_id}",
                temperature_c=34.0,
                delta_c=6.0,
                recorded_at=t0,
                status="alert",
                ewma_delta_c=3.0,
                cusum_score=12.0,
                bayes_post=(0.05, 0.05, 0.9),
                bayes_mu_fault=4.0,
                alert_active=True,
                alert_armed=True,
                rearm_after=None,
                open_alert=("bayesian_hmm", "high", "Overheat", 0.9),
            )
        b2_alert = store.list_alerts(bay_id="B2", status="open")[0]
        store.acknowledge_alert(b2_alert.id)

        b1_eq = store.update_baseline("B1", 31.0)
        b2_eq = store.update_baseline("B2", 31.5)

        assert b1_eq.status == "nominal"
        assert b1_eq.alert_active is False
        assert b1_eq.last_reading_at == t0
        assert b2_eq.status == "nominal"
        assert b2_eq.alert_active is False
        assert store.list_alerts(bay_id="B1", status="open") == []
        assert store.list_alerts(bay_id="B2", status="acknowledged") == []
        assert store.list_alerts(bay_id="B1", status="resolved")[0].resolved_at is not None
        assert store.list_alerts(bay_id="B2", status="resolved")[0].resolved_at is not None
        assert len(store.list_alerts(bay_id="B3", status="open")) == 1
