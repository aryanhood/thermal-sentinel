from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from thermal_sentinel import monitoring as monitoring_module
from thermal_sentinel.monitoring import (
    MonitoringService,
    ReadingInput,
    update_bayesian_hmm,
    update_cusum,
    update_ewma,
)
from thermal_sentinel.simulation import Policy, PolicyConfig
from thermal_sentinel.storage import (
    ConflictingReadingError,
    EquipmentNotFoundError,
    InvalidTimestampError,
    MonitoringStore,
    SensorMismatchError,
)


def test_pure_detector_step_functions_and_config_validation() -> None:
    assert update_ewma(0.0, 2.0, 0.3) == pytest.approx(0.6)
    assert update_cusum(0.0, 0.2, 0.6) == 0.0
    assert update_cusum(0.0, 2.6, 0.6) == pytest.approx(2.0)

    post, mu = update_bayesian_hmm((0.995, 0.004, 0.001), 1.2, 0.0)
    assert sum(post) == pytest.approx(1.0)
    assert post[0] > 0.95
    assert mu == pytest.approx(1.2, abs=1e-4)

    post_high, mu_high = update_bayesian_hmm((0.1, 0.1, 0.8), 1.2, 5.0)
    assert sum(post_high) == pytest.approx(1.0)
    assert mu_high > 1.2

    with pytest.raises(ValueError, match="finite"):
        update_ewma(math.nan, 1.0, 0.3)
    with pytest.raises(ValueError, match="finite"):
        update_cusum(0.0, math.inf, 0.6)
    with pytest.raises(ValueError, match="finite"):
        update_bayesian_hmm((0.995, 0.004, 0.001), 1.2, math.nan)

    with pytest.raises(ValueError, match="finite"):
        PolicyConfig(ewma_alpha=math.nan)
    with pytest.raises(ValueError, match="ewma_alpha"):
        PolicyConfig(ewma_alpha=0.0)
    with pytest.raises(ValueError, match="alert_off_c"):
        PolicyConfig(alert_on_c=1.0, alert_off_c=1.2)
    with pytest.raises(ValueError, match="watch_c"):
        PolicyConfig(watch_c=2.0, confirm_c=1.5)
    with pytest.raises(ValueError, match="rearm_after_s"):
        PolicyConfig(rearm_after_s=-1.0)
    with pytest.raises(ValueError, match="guardrail_fraction"):
        PolicyConfig(guardrail_fraction=1.5)
    with pytest.raises(ValueError, match="cusum_threshold_c"):
        PolicyConfig(cusum_threshold_c=0.0)
    with pytest.raises(ValueError, match="bayes probabilities"):
        PolicyConfig(bayes_alert_prob=0.2, bayes_clear_prob=0.5)
    with pytest.raises(ValueError, match="scale parameters"):
        PolicyConfig(thermal_scale_c=0.0)


def test_sensor_binding_dedup_conflict_and_timestamp_policies() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        with pytest.raises(ValueError, match="negative"):
            MonitoringService(store, max_future_skew_s=-1.0)

        service = MonitoringService(store, policy=Policy.ADAPTIVE, max_future_skew_s=5.0, clock=lambda: t0)

        with pytest.raises(SensorMismatchError, match="thermocouple-B1"):
            service.ingest_reading("B1", 34.0, sensor_id="wrong-sensor", recorded_at=t0)

        # Future timestamp beyond 5s skew is rejected; within 5s skew is accepted
        with pytest.raises(InvalidTimestampError, match="skew"):
            service.ingest_reading("B1", 28.5, recorded_at=t0 + timedelta(seconds=6))
        first = service.ingest_reading("B1", 34.0, recorded_at=t0 + timedelta(seconds=4))
        assert first.alert_event == "opened"
        assert first.duplicate is False

        # Exact retry returns duplicate=True and does not double-apply detector or create duplicate alerts
        before_eq = store.get_equipment("B1")
        retry = service.ingest_reading("B1", 34.0, recorded_at=t0 + timedelta(seconds=4))
        assert retry.duplicate is True
        assert retry.applied_to_detector is False
        assert retry.alert is None
        assert store.get_equipment("B1") == before_eq
        assert len(store.list_readings(bay_id="B1")) == 1
        assert len(store.list_alerts(bay_id="B1")) == 1

        # Conflicting temperature at existing timestamp raises ConflictingReadingError
        with pytest.raises(ConflictingReadingError, match="Conflicting reading"):
            service.ingest_reading("B1", 29.0, recorded_at=t0 + timedelta(seconds=4))

        # Server-assigned reading (recorded_at=None) advances monotonically past last_reading_at
        mono = service.ingest_reading("B1", 28.0)
        assert mono.reading.recorded_at > before_eq.last_reading_at  # type: ignore[operator]


def test_bayesian_rejects_isolated_glitch_and_catches_sustained_ramp() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.BAYESIAN, clock=lambda: t0 + timedelta(hours=1))

        for idx in range(3):
            res = service.ingest_reading("B1", 31.0, recorded_at=t0 + timedelta(seconds=idx * 0.5))
            assert res.alert_event is None
            assert res.equipment.alert_active is False

        opened = False
        for step in range(1, 20):
            temp = 28.0 if step <= 3 else 28.0 + min(6.0, 0.4 * (step - 3))
            res = service.ingest_reading("B1", temp, recorded_at=t0 + timedelta(seconds=(3 + step) * 0.5))
            if res.alert_event == "opened":
                opened = True
                assert res.alert is not None
                assert res.alert.detector == "bayesian_hmm"
                break
        assert opened is True

        resolved = False
        for cool in range(1, 25):
            res = service.ingest_reading("B1", 28.0, recorded_at=t0 + timedelta(seconds=(30 + cool) * 0.5))
            if res.alert_event == "resolved":
                resolved = True
                assert res.alert is not None
                assert res.alert.status == "resolved"
                break
        assert resolved is True


def test_cusum_and_ewma_alert_lifecycle_and_rearm() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        cusum_svc = MonitoringService(store, policy=Policy.CUSUM, clock=lambda: t0 + timedelta(minutes=5))
        opened_cusum = False
        for step in range(12):
            res = cusum_svc.ingest_reading("B2", 31.0, recorded_at=t0 + timedelta(seconds=step))
            if res.alert_event == "opened":
                opened_cusum = True
                assert res.alert is not None
                assert res.alert.detector == "cusum"
                break
        assert opened_cusum is True

        ewma_svc = MonitoringService(store, policy=Policy.ADAPTIVE, clock=lambda: t0 + timedelta(minutes=5))
        store.record_verification(
            bay_id="B3",
            policy="adaptive",
            reason="False alarm cleared",
            robot_x_m=0.0,
            robot_y_m=3.0,
            camera_temp_c=28.0,
            outcome="false_alarm",
            inspected_at=t0,
            status="nominal",
            cusum_score=0.0,
            bayes_post=(0.995, 0.004, 0.001),
            bayes_mu_fault=1.2,
            alert_active=False,
            alert_armed=False,
            rearm_after=t0 + timedelta(seconds=15),
            alert_resolution_status="resolved",
        )
        suppressed = ewma_svc.ingest_reading("B3", 33.0, recorded_at=t0 + timedelta(seconds=2))
        assert suppressed.equipment.alert_armed is False
        assert suppressed.equipment.alert_active is False

        rearmed = ewma_svc.ingest_reading("B3", 33.0, recorded_at=t0 + timedelta(seconds=20))
        assert rearmed.equipment.alert_armed is True
        assert rearmed.alert_event == "opened"

        store.record_verification(
            bay_id="B4",
            policy="adaptive",
            reason="Confirmed",
            robot_x_m=8.0,
            robot_y_m=5.0,
            camera_temp_c=31.0,
            outcome="confirmed",
            inspected_at=t0,
            status="confirmed",
            cusum_score=0.0,
            bayes_post=(0.0, 0.0, 1.0),
            bayes_mu_fault=3.0,
            alert_active=False,
            alert_armed=True,
            rearm_after=None,
            alert_resolution_status="confirmed",
        )
        after_confirm = ewma_svc.ingest_reading("B4", 35.0, recorded_at=t0 + timedelta(seconds=5))
        assert after_confirm.equipment.status == "confirmed"
        assert after_confirm.alert_event is None


def test_atomic_batch_rollback_on_failure() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.ADAPTIVE, clock=lambda: t0)
        baseline_b1 = store.get_equipment("B1")

        with pytest.raises(EquipmentNotFoundError, match="B99"):
            service.ingest_batch(
                [
                    ReadingInput(bay_id="B1", temperature_c=34.0, recorded_at=t0),
                    ReadingInput(bay_id="B99", temperature_c=29.0, recorded_at=t0),
                ]
            )
        # B1 reading and alert from item 0 must have rolled back completely
        assert store.list_readings() == []
        assert store.list_alerts() == []
        assert store.get_equipment("B1") == baseline_b1

        results = service.ingest_batch(
            [
                ReadingInput(bay_id="B1", temperature_c=28.1, recorded_at=t0),
                ReadingInput(bay_id="B2", temperature_c=28.2, recorded_at=t0),
            ]
        )
        assert len(results) == 2
        assert len(store.list_readings()) == 2


def test_multi_connection_concurrent_ingestion_and_duplicate_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "multi_conn.db"
    real_cusum = monitoring_module.update_cusum
    counter_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def slow_cusum(prev_cusum: float, delta_c: float, drift_c: float) -> float:
        nonlocal in_flight, max_in_flight
        with counter_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.002)
        try:
            return real_cusum(prev_cusum, delta_c, drift_c)
        finally:
            with counter_lock:
                in_flight -= 1

    monkeypatch.setattr(monitoring_module, "update_cusum", slow_cusum)

    # Two independent MonitoringStore connections (separate Python locks) hitting the same DB
    with MonitoringStore(db_file) as store_a, MonitoringStore(db_file) as store_b:
        svc_a = MonitoringService(store_a, policy=Policy.CUSUM)
        svc_b = MonitoringService(store_b, policy=Policy.CUSUM)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit((svc_a if idx % 2 == 0 else svc_b).ingest_reading, "B1", 30.0) for idx in range(24)]
            results = [future.result() for future in futures]

        assert len(results) == 24
        assert all(not r.duplicate for r in results)
        assert len(store_a.list_readings(bay_id="B1", limit=100)) == 24
        assert store_a.get_equipment("B1").cusum_score == pytest.approx(24 * 1.4)

    # Concurrent exact retries from two connections produce exactly 1 new write and 15 duplicates
    t_retry = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
    with MonitoringStore(tmp_path / "retry_conn.db") as store_1, MonitoringStore(tmp_path / "retry_conn.db") as store_2:
        svc_1 = MonitoringService(store_1, policy=Policy.ADAPTIVE, clock=lambda: t_retry)
        svc_2 = MonitoringService(store_2, policy=Policy.ADAPTIVE, clock=lambda: t_retry)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit((svc_1 if idx % 2 == 0 else svc_2).ingest_reading, "B2", 34.0, recorded_at=t_retry)
                for idx in range(16)
            ]
            retry_res = [f.result() for f in futures]
        assert sum(1 for r in retry_res if not r.duplicate) == 1
        assert sum(1 for r in retry_res if r.duplicate) == 15
        assert len(store_1.list_readings(bay_id="B2")) == 1
        assert len(store_1.list_alerts(bay_id="B2")) == 1


def test_out_of_order_readings_do_not_corrupt_detector_or_alert_state() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.ADAPTIVE, clock=lambda: t0 + timedelta(minutes=1))
        for sec in (0, 2, 4, 10):
            service.ingest_reading("B1", 33.0, recorded_at=t0 + timedelta(seconds=sec))

        before_eq = store.get_equipment("B1")
        before_alerts = store.list_alerts(bay_id="B1")
        assert before_eq.status == "alert"
        assert before_eq.alert_active is True

        late = service.ingest_reading("B1", 28.0, recorded_at=t0 + timedelta(seconds=3))
        assert late.out_of_order is True
        assert late.applied_to_detector is False
        assert late.duplicate is False
        assert late.alert is None
        assert late.alert_event is None
        assert late.reading.out_of_order is True
        assert late.equipment == before_eq
        assert store.get_equipment("B1") == before_eq
        assert store.list_alerts(bay_id="B1") == before_alerts
        assert any(r.out_of_order for r in store.list_readings(bay_id="B1"))
