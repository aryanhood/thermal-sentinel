from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from thermal_sentinel.inspection import InspectionPlanner
from thermal_sentinel.monitoring import MonitoringService
from thermal_sentinel.simulation import Policy
from thermal_sentinel.storage import InvalidTimestampError, MonitoringStore, StaleInspectionError


def test_planner_validation_and_empty_store() -> None:
    with MonitoringStore(":memory:", seed_defaults=False) as store:
        with pytest.raises(ValueError, match="finite"):
            InspectionPlanner(store, robot_speed_m_s=math.nan)
        with pytest.raises(ValueError, match="positive"):
            InspectionPlanner(store, robot_speed_m_s=0.0)
        with pytest.raises(ValueError, match="negative"):
            InspectionPlanner(store, close_inspection_s=-1.0)

        planner = InspectionPlanner(store)
        with pytest.raises(ValueError, match="No equipment bays"):
            planner.next_target()
        with pytest.raises(ValueError, match="No equipment bays"):
            planner.next_target(log_dispatch=True)


def test_priority_policies_wall_clock_guardrail_and_tie_breaking() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    now_ref = [t0]
    with MonitoringStore(":memory:") as store:
        planner = InspectionPlanner(store, policy=Policy.BAYESIAN, clock=lambda: now_ref[0])
        service = MonitoringService(store, policy=Policy.ADAPTIVE, clock=lambda: now_ref[0])

        first = planner.next_target(now=t0, log_dispatch=True)
        assert first.mode == "patrol"
        assert first.bay_id == "B1"
        assert len(store.list_inspection_logs(bay_id="B1")) == 1

        # Inspect all bays at the same timestamp -> tie-break on guardrail must pick B1 (lowest id)
        for bay_id in ("B4", "B1", "B2", "B3", "B5", "B6"):
            planner.verify_inspection(bay_id, 28.0, inspected_at=t0)

        patrol = planner.next_target(now=t0 + timedelta(seconds=40), policy=Policy.ADAPTIVE)
        assert patrol.mode == "patrol"
        assert patrol.bay_id == "B1"

        # Advance wall clock without passing explicit_now or ingesting new readings -> guardrail triggers
        now_ref[0] = t0 + timedelta(seconds=80)
        wall_guardrail = planner.next_target(policy=Policy.ADAPTIVE)
        assert wall_guardrail.mode == "guardrail"
        assert wall_guardrail.bay_id == "B1"

        # GREEDY_EWMA ignores guardrail when no alerts are active
        assert planner.next_target(policy=Policy.GREEDY_EWMA).mode == "patrol"

        # Trigger alerts on B3 (far, low delta) and B5 (closer, hotter) with advancing wall clock
        for step in range(4):
            ts_step = t0 + timedelta(seconds=112 + step)
            now_ref[0] = ts_step
            service.ingest_reading("B3", 31.0, recorded_at=ts_step)
            service.ingest_reading("B5", 34.0, recorded_at=ts_step)

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


def test_verification_outcomes_and_stale_inspection_rejection() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.ADAPTIVE, clock=lambda: t0 + timedelta(minutes=5))
        planner = InspectionPlanner(store, policy=Policy.ADAPTIVE, clock=lambda: t0 + timedelta(minutes=5))

        for step in range(3):
            service.ingest_reading("B5", 33.0, recorded_at=t0 + timedelta(seconds=20 + step))
        assert store.get_active_alert("B5") is not None

        # Reject verification timestamp older than the active alert's created_at
        with pytest.raises(StaleInspectionError, match="active alert"):
            planner.verify_inspection("B5", 28.0, inspected_at=t0 + timedelta(seconds=1))

        # Reject future verification timestamp beyond skew
        with pytest.raises(InvalidTimestampError, match="skew"):
            planner.verify_inspection("B5", 32.0, inspected_at=t0 + timedelta(minutes=10))

        # Watch outcome (inconclusive 1.0..1.5 C delta)
        watch_log, watch_eq = planner.verify_inspection("B5", 29.2, inspected_at=t0 + timedelta(seconds=25))
        assert watch_log.outcome == "watch"
        assert watch_eq.alert_active is True

        # Reject verification older than last_inspected_at
        with pytest.raises(StaleInspectionError, match="last_inspected_at"):
            planner.verify_inspection("B5", 28.0, inspected_at=t0 + timedelta(seconds=22))

        # Confirm hotspot (>= 1.5 C delta)
        conf_log, conf_eq = planner.verify_inspection("B5", 31.0, inspected_at=t0 + timedelta(seconds=30))
        assert conf_log.outcome == "confirmed"
        assert conf_eq.status == "confirmed"
        assert conf_eq.alert_active is False
        assert store.list_alerts(status="confirmed", bay_id="B5")

        # False alarm clearing on an alerted bay (B1)
        for step in range(3):
            service.ingest_reading("B1", 32.0, recorded_at=t0 + timedelta(seconds=40 + step))
        fa_log, fa_eq = planner.verify_inspection("B1", 28.1, inspected_at=t0 + timedelta(seconds=45))
        assert fa_log.outcome == "false_alarm"
        assert fa_eq.alert_active is False
        assert fa_eq.alert_armed is False
        assert fa_eq.rearm_after is not None

        # Routine nominal check using default clock-based timestamp
        nom_log, nom_eq = planner.verify_inspection("B2", 28.0)
        assert nom_log.outcome == "nominal"
        assert nom_eq.status == "nominal"


def test_automatic_verification_timestamp_respects_clock_skew() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    now_ref = [t0 + timedelta(seconds=30)]
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.ADAPTIVE, max_future_skew_s=5.0, clock=lambda: now_ref[0])
        planner = InspectionPlanner(store, policy=Policy.ADAPTIVE, max_future_skew_s=5.0, clock=lambda: now_ref[0])

        # Verify B1 at t0 + 30s, then step clock back to t0 (exceeding 5s skew)
        planner.verify_inspection("B1", 28.0)
        now_ref[0] = t0
        with pytest.raises(InvalidTimestampError, match="Automatic inspection timestamp"):
            planner.verify_inspection("B1", 28.0)

        # Open an active alert on B2 at t0 + 30s, then step clock back to t0
        now_ref[0] = t0 + timedelta(seconds=30)
        for offset in range(3):
            service.ingest_reading("B2", 33.0, recorded_at=t0 + timedelta(seconds=28 + offset))
        assert store.get_active_alert("B2") is not None
        now_ref[0] = t0
        with pytest.raises(InvalidTimestampError, match="Automatic inspection timestamp"):
            planner.verify_inspection("B2", 28.0)


def test_simulation_replay_is_completely_isolated_from_operational_store() -> None:
    t0 = datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC)
    with MonitoringStore(":memory:") as store:
        service = MonitoringService(store, policy=Policy.ADAPTIVE, clock=lambda: t0 + timedelta(minutes=1))
        planner = InspectionPlanner(store, policy=Policy.ADAPTIVE, clock=lambda: t0 + timedelta(minutes=1))

        for sec in range(3):
            service.ingest_reading("B1", 34.0, recorded_at=t0 + timedelta(seconds=sec))
        planner.next_target(now=t0 + timedelta(seconds=3), log_dispatch=True)

        eq_before = store.list_equipment()
        readings_before = store.list_readings(limit=1000)
        alerts_before = store.list_alerts(limit=1000)
        logs_before = store.list_inspection_logs(limit=1000)

        summary = planner.replay_scenario(policy=Policy.BAYESIAN, seed=3, fault_bay_id="B5", max_ticks=140)
        assert summary.case_id == "seed-3-b5"
        assert summary.alerts_opened >= 1
        assert summary.recommended_bay == "B5"
        assert summary.recommended_mode == "alert"

        # Operational store must remain 100% untouched
        assert store.list_equipment() == eq_before
        assert store.list_readings(limit=1000) == readings_before
        assert store.list_alerts(limit=1000) == alerts_before
        assert store.list_inspection_logs(limit=1000) == logs_before

        clean_summary = planner.replay_scenario(service, seed=1, fault_bay_id=None, max_ticks=10)
        assert clean_summary.readings_ingested == 60
        assert clean_summary.fault_bay_id is None
