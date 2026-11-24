from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from thermal_sentinel.inspection import InspectionPlanner
from thermal_sentinel.monitoring import MonitoringService
from thermal_sentinel.simulation import Policy
from thermal_sentinel.storage import MonitoringStore


def test_empty_store_raises_on_planning() -> None:
    with MonitoringStore(":memory:", seed_defaults=False) as store:
        planner = InspectionPlanner(store)
        with pytest.raises(ValueError, match="No equipment bays"):
            planner.next_target()


def test_priority_policies_and_guardrail() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        planner = InspectionPlanner(store, policy=Policy.BAYESIAN)
        service = MonitoringService(store, policy=Policy.ADAPTIVE)

        # Initial patrol picks B1 when none have been inspected
        first = planner.next_target(now=t0, log_dispatch=True)
        assert first.mode == "patrol"
        assert first.bay_id == "B1"
        assert len(store.list_inspection_logs(bay_id="B1")) == 1

        # Routine patrol when all bays were recently inspected (< 72 s ago)
        for offset_s, bay_id in enumerate(["B4", "B1", "B2", "B3", "B5", "B6"]):
            planner.verify_inspection(
                bay_id,
                28.0,
                inspected_at=t0 + timedelta(seconds=offset_s * 5),
            )
        patrol = planner.next_target(now=t0 + timedelta(seconds=40), policy=Policy.ADAPTIVE)
        assert patrol.mode == "patrol"
        assert patrol.bay_id == "B4"

        # Coverage guardrail triggers once B4 goes >= 72 s unseen while no alerts are active
        guardrail = planner.next_target(now=t0 + timedelta(seconds=80), policy=Policy.ADAPTIVE)
        assert guardrail.mode == "guardrail"
        assert guardrail.bay_id == "B4"

        # Trigger alerts on B3 (far, low delta) and B5 (closer, hotter)
        for _ in range(4):
            service.ingest_reading("B3", 31.0, recorded_at=t0 + timedelta(seconds=112))
            service.ingest_reading("B5", 34.0, recorded_at=t0 + timedelta(seconds=112))

        adaptive_pick = planner.next_target(robot_x_m=5.0, robot_y_m=5.0, policy=Policy.ADAPTIVE)
        greedy_pick = planner.next_target(robot_x_m=8.0, robot_y_m=1.0, policy=Policy.GREEDY_EWMA)
        cusum_pick = planner.next_target(robot_x_m=5.0, robot_y_m=5.0, policy=Policy.CUSUM)
        bayes_pick = planner.next_target(robot_x_m=5.0, robot_y_m=5.0, policy=Policy.BAYESIAN)
        fixed_pick = planner.next_target(robot_x_m=5.0, robot_y_m=5.0, policy=Policy.FIXED)

        assert adaptive_pick.mode == "alert"
        assert adaptive_pick.bay_id == "B5"
        assert greedy_pick.mode == "alert"
        assert greedy_pick.bay_id == "B3"
        assert cusum_pick.mode == "alert"
        assert bayes_pick.mode == "alert"
        assert bayes_pick.close_inspection is True
        assert fixed_pick.mode == "patrol"


def test_verification_outcomes_and_simulation_replay() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.BAYESIAN)
        planner = InspectionPlanner(store, policy=Policy.BAYESIAN)

        # Replay known fault scenario (seed=3, fault on B5)
        summary = planner.replay_scenario(service, seed=3, fault_bay_id="B5", max_ticks=140)
        assert summary.case_id == "seed-3-b5"
        assert summary.alerts_opened >= 1
        assert summary.recommended_bay == "B5"
        assert summary.recommended_mode == "alert"

        # Watch outcome (inconclusive 1.0..1.5 C above B5's seeded baseline)
        b5_base = store.get_equipment("B5").baseline_temp_c
        watch_log, watch_eq = planner.verify_inspection("B5", b5_base + 1.2, inspected_at=t0)
        assert watch_log.outcome == "watch"
        assert watch_eq.alert_active is True

        # Confirmed outcome (>= 1.5 C above B5's seeded baseline)
        conf_log, conf_eq = planner.verify_inspection("B5", b5_base + 3.0, inspected_at=t0 + timedelta(seconds=6))
        assert conf_log.outcome == "confirmed"
        assert conf_eq.status == "confirmed"
        assert conf_eq.alert_active is False
        assert store.list_alerts(status="confirmed", bay_id="B5")

        # False alarm clearing on an alerted bay
        adaptive_svc = MonitoringService(store, policy=Policy.ADAPTIVE)
        for step in range(4):
            adaptive_svc.ingest_reading("B1", 32.0, recorded_at=t0 + timedelta(seconds=10 + step))
        assert store.get_equipment("B1").alert_active is True
        b1_base = store.get_equipment("B1").baseline_temp_c
        fa_log, fa_eq = planner.verify_inspection("B1", b1_base + 0.1, inspected_at=t0 + timedelta(seconds=20))
        assert fa_log.outcome == "false_alarm"
        assert fa_eq.alert_active is False
        assert fa_eq.alert_armed is False
        assert fa_eq.rearm_after is not None

        # Routine nominal check on un-alerted bay (and with default inspected_at=None)
        b2_base = store.get_equipment("B2").baseline_temp_c
        nom_log, nom_eq = planner.verify_inspection("B2", b2_base)
        assert nom_log.outcome == "nominal"
        assert nom_eq.status == "nominal"


def test_replay_on_empty_store_without_reset() -> None:
    with MonitoringStore(":memory:", seed_defaults=False) as store:
        service = MonitoringService(store, policy=Policy.BAYESIAN)
        planner = InspectionPlanner(store, policy=Policy.BAYESIAN)
        summary = planner.replay_scenario(service, seed=1, fault_bay_id=None, max_ticks=10, reset_first=False)
        assert summary.readings_ingested == 60
        assert summary.fault_bay_id is None
        assert len(store.list_equipment()) == 6
