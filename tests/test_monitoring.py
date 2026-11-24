from __future__ import annotations

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
from thermal_sentinel.simulation import Policy
from thermal_sentinel.storage import EquipmentNotFoundError, MonitoringStore


def test_pure_detector_step_functions() -> None:
    assert update_ewma(0.0, 2.0, 0.3) == pytest.approx(0.6)
    assert update_cusum(0.0, 0.2, 0.6) == 0.0
    assert update_cusum(0.0, 2.6, 0.6) == pytest.approx(2.0)

    post, mu = update_bayesian_hmm((0.995, 0.004, 0.001), 1.2, 0.0)
    assert sum(post) == pytest.approx(1.0)
    assert post[0] > 0.95
    assert mu == pytest.approx(1.2, abs=1e-4)

    # Naive timestamp branch in MonitoringService
    with MonitoringStore(":memory:") as store:
        naive_svc = MonitoringService(store, policy=Policy.ADAPTIVE)
        res = naive_svc.ingest_reading("B1", 28.0, recorded_at=datetime(2026, 1, 1, 0, 0, 0))
        assert res.reading.recorded_at.tzinfo is UTC

    post_high, mu_high = update_bayesian_hmm((0.1, 0.1, 0.8), 1.2, 5.0)
    assert sum(post_high) == pytest.approx(1.0)
    assert mu_high > 1.2


def test_bayesian_rejects_isolated_glitch_and_catches_sustained_ramp() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.BAYESIAN)

        # Transient 3.0 C spike lasting 3 ticks should be attributed to GLITCH, not FAULT_RAMP
        for idx in range(3):
            res = service.ingest_reading("B1", 31.0, recorded_at=t0 + timedelta(seconds=idx * 0.5))
            assert res.alert_event is None
            assert res.equipment.alert_active is False

        # Return to nominal, then inject sustained ramp (+2.0 C to +5.0 C)
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

        # Cool equipment back down until Bayesian posterior clears
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
        cusum_svc = MonitoringService(store, policy=Policy.CUSUM)
        opened_cusum = False
        for step in range(12):
            res = cusum_svc.ingest_reading("B2", 31.0, recorded_at=t0 + timedelta(seconds=step))
            if res.alert_event == "opened":
                opened_cusum = True
                assert res.alert is not None
                assert res.alert.detector == "cusum"
                break
        assert opened_cusum is True

        ewma_svc = MonitoringService(store, policy=Policy.ADAPTIVE)
        # Disarm B3 manually with a future rearm_after timestamp to verify suppression & rearm
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

        # Confirmed equipment stays confirmed and does not emit new alerts
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


def test_ingest_batch_validates_all_bays_before_writing() -> None:
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.ADAPTIVE)
        with pytest.raises(EquipmentNotFoundError, match="B99"):
            service.ingest_batch(
                [
                    ReadingInput(bay_id="B1", temperature_c=28.5),
                    ReadingInput(bay_id="B99", temperature_c=29.0),
                ]
            )
        assert store.list_readings() == []

        results = service.ingest_batch(
            [
                ReadingInput(bay_id="B1", temperature_c=28.1),
                ReadingInput(bay_id="B2", temperature_c=28.2),
            ]
        )
        assert len(results) == 2
        assert len(store.list_readings()) == 2


def test_concurrent_readings_serialize_read_compute_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    real_cusum = monitoring_module.update_cusum
    counter_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def tracked_cusum(prev_cusum: float, delta_c: float, drift_c: float) -> float:
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

    monkeypatch.setattr(monitoring_module, "update_cusum", tracked_cusum)

    with MonitoringStore(tmp_path / "concurrent.db") as store:
        service = MonitoringService(store, policy=Policy.CUSUM)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(service.ingest_reading, "B1", 30.0, recorded_at=t0) for _ in range(32)]
            results = [future.result() for future in futures]

        assert len(results) == 32
        assert max_in_flight == 1
        assert len(store.list_readings(bay_id="B1", limit=100)) == 32
        # Every reading adds (2.0 - 0.6) = 1.4 with zero lost updates across threads
        assert store.get_equipment("B1").cusum_score == pytest.approx(32 * 1.4)


def test_out_of_order_readings_do_not_corrupt_detector_or_alert_state() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.ADAPTIVE)
        for sec in (0, 2, 4, 10):
            service.ingest_reading("B1", 33.0, recorded_at=t0 + timedelta(seconds=sec))

        before_eq = store.get_equipment("B1")
        before_alerts = store.list_alerts(bay_id="B1")
        assert before_eq.status == "alert"
        assert before_eq.alert_active is True

        late = service.ingest_reading("B1", 28.0, sensor_id="delayed-probe", recorded_at=t0 + timedelta(seconds=3))
        assert late.out_of_order is True
        assert late.applied_to_detector is False
        assert late.alert is None
        assert late.alert_event is None
        assert late.reading.out_of_order is True
        assert late.equipment == before_eq
        assert store.get_equipment("B1") == before_eq
        assert store.list_alerts(bay_id="B1") == before_alerts
        assert any(r.out_of_order and r.sensor_id == "delayed-probe" for r in store.list_readings(bay_id="B1"))
