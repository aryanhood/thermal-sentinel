"""Inspection prioritization planner and isolated simulation replay."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .environment import Environment
from .model import make_scenario
from .monitoring import MonitoringService
from .simulation import Policy, PolicyConfig
from .storage import (
    AlertRecord,
    EquipmentRecord,
    InspectionLogRecord,
    InvalidTimestampError,
    MonitoringStore,
    StaleInspectionError,
)

__all__ = ["InspectionDecision", "InspectionPlanner", "ReplaySummary", "run_isolated_replay"]


@dataclass(frozen=True, slots=True)
class InspectionDecision:
    """Next recommended equipment bay and the reasoning behind it."""

    bay_id: str
    bay_label: str
    mode: str
    policy: Policy
    priority_score: float
    distance_m: float
    estimated_travel_s: float
    close_inspection: bool
    reason: str
    scores: dict[str, float]


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    """Summary returned after streaming a simulated scenario through an isolated pipeline."""

    case_id: str
    policy: Policy
    ticks_ingested: int
    readings_ingested: int
    alerts_opened: int
    recommended_bay: str
    recommended_mode: str
    recommended_reason: str
    fault_bay_id: str | None
    fault_onset_s: float


def _clip(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _require_finite(*values: float) -> None:
    if not all(math.isfinite(val) for val in values):
        raise ValueError("Numeric inspection parameters must be finite")


def _to_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def run_isolated_replay(
    *,
    policy: Policy | str = Policy.BAYESIAN,
    config: PolicyConfig | None = None,
    seed: int = 3,
    fault_bay_id: str | None = "B5",
    max_ticks: int = 120,
) -> ReplaySummary:
    """Stream a simulated scenario through a fresh in-memory store without touching operational data."""
    scenario = make_scenario(seed, fault_bay_id)
    env = Environment(scenario)
    resolved_policy = Policy(policy)
    start_ts = datetime(2026, 1, 1, tzinfo=UTC)
    ticks_to_run = max(1, min(max_ticks, scenario.ticks))
    last_ts = start_ts + timedelta(seconds=(ticks_to_run - 1) * scenario.dt_s)

    with MonitoringStore(":memory:", seed_defaults=False) as sim_store:
        for bay in scenario.bays:
            sim_store.create_equipment(bay.id, bay.label, bay.x, bay.y, env.baseline_c[bay.id])
        sim_service = MonitoringService(
            sim_store,
            policy=resolved_policy,
            config=config,
            clock=lambda: last_ts,
        )
        sim_planner = InspectionPlanner(
            sim_store,
            policy=resolved_policy,
            config=config,
            clock=lambda: last_ts,
        )
        readings_ingested = 0
        alerts_opened = 0
        for tick in range(ticks_to_run):
            ts = start_ts + timedelta(seconds=tick * scenario.dt_s)
            for bay_id in env.bay_ids:
                temp_c = env.baseline_c[bay_id] + env.line_delta_c(bay_id, tick)
                res = sim_service.ingest_reading(bay_id, temp_c, recorded_at=ts)
                readings_ingested += 1
                if res.alert_event == "opened":
                    alerts_opened += 1

        decision = sim_planner.next_target(now=last_ts, policy=resolved_policy)
        return ReplaySummary(
            case_id=scenario.case_id,
            policy=resolved_policy,
            ticks_ingested=ticks_to_run,
            readings_ingested=readings_ingested,
            alerts_opened=alerts_opened,
            recommended_bay=decision.bay_id,
            recommended_mode=decision.mode,
            recommended_reason=decision.reason,
            fault_bay_id=scenario.fault_bay_id,
            fault_onset_s=scenario.fault_onset_s,
        )


class InspectionPlanner:
    """Uses thermal alerts and patrol staleness to select the next equipment inspection target."""

    def __init__(
        self,
        store: MonitoringStore,
        policy: Policy | str = Policy.BAYESIAN,
        config: PolicyConfig | None = None,
        *,
        robot_speed_m_s: float = 1.0,
        close_inspection_s: float = 6.0,
        max_unseen_s: float = 90.0,
        max_future_skew_s: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _require_finite(robot_speed_m_s, close_inspection_s, max_unseen_s, max_future_skew_s)
        if robot_speed_m_s <= 0.0 or max_unseen_s <= 0.0:
            raise ValueError("robot_speed_m_s and max_unseen_s must be positive")
        if close_inspection_s < 0.0 or max_future_skew_s < 0.0:
            raise ValueError("close_inspection_s and max_future_skew_s cannot be negative")
        self.store = store
        self.policy = Policy(policy)
        self.cfg = config or PolicyConfig()
        self.robot_speed_m_s = robot_speed_m_s
        self.close_inspection_s = close_inspection_s
        self.max_unseen_s = max_unseen_s
        self.max_future_skew_s = max_future_skew_s
        self._clock: Callable[[], datetime] = clock if clock is not None else (lambda: datetime.now(UTC))

    def next_target(
        self,
        *,
        robot_x_m: float = 0.0,
        robot_y_m: float = 3.0,
        now: datetime | None = None,
        policy: Policy | str | None = None,
        log_dispatch: bool = False,
    ) -> InspectionDecision:
        """Select the highest-priority bay for the inspection robot to visit next."""
        _require_finite(robot_x_m, robot_y_m)
        active_policy = self.policy if policy is None else Policy(policy)

        if log_dispatch:
            with self.store.transaction():
                equipment = self.store.list_equipment()
                if not equipment:
                    raise ValueError("No equipment bays registered for inspection planning")
                ref_ts = self._reference_time(equipment, now)
                decision = self._decide(equipment, active_policy, robot_x_m, robot_y_m, ref_ts)
                self.store.record_inspection_log(
                    bay_id=decision.bay_id,
                    mode=decision.mode,
                    policy=decision.policy.value,
                    priority_score=decision.priority_score,
                    reason=decision.reason,
                    robot_x_m=robot_x_m,
                    robot_y_m=robot_y_m,
                    camera_temp_c=None,
                    outcome="dispatched",
                    created_at=ref_ts,
                )
                return decision

        equipment = self.store.list_equipment()
        if not equipment:
            raise ValueError("No equipment bays registered for inspection planning")
        ref_ts = self._reference_time(equipment, now)
        return self._decide(equipment, active_policy, robot_x_m, robot_y_m, ref_ts)

    def verify_inspection(
        self,
        bay_id: str,
        camera_temp_c: float,
        *,
        robot_x_m: float = 0.0,
        robot_y_m: float = 3.0,
        inspected_at: datetime | None = None,
    ) -> tuple[InspectionLogRecord, EquipmentRecord]:
        """Submit a close-range thermal camera check and update alert/equipment state atomically."""
        _require_finite(camera_temp_c, robot_x_m, robot_y_m)
        now_utc = _to_utc(self._clock())

        with self.store.transaction():
            eq = self.store.get_equipment(bay_id)
            active_alert = self.store.get_active_alert(bay_id)
            ts = self._resolve_verification_timestamp(eq, active_alert, inspected_at, now_utc)
            state = self._classify_verification(eq, camera_temp_c, ts)
            return self.store.record_verification(
                bay_id=bay_id,
                policy=self.policy.value,
                reason=state[0],
                robot_x_m=robot_x_m,
                robot_y_m=robot_y_m,
                camera_temp_c=camera_temp_c,
                outcome=state[1],
                inspected_at=ts,
                status=state[2],
                cusum_score=state[3],
                bayes_post=state[4],
                bayes_mu_fault=state[5],
                alert_active=state[6],
                alert_armed=state[7],
                rearm_after=state[8],
                alert_resolution_status=state[9],
            )

    def _resolve_verification_timestamp(
        self,
        eq: EquipmentRecord,
        active_alert: AlertRecord | None,
        inspected_at: datetime | None,
        now_utc: datetime,
    ) -> datetime:
        if inspected_at is not None:
            ts = _to_utc(inspected_at)
            if ts > now_utc + timedelta(seconds=self.max_future_skew_s):
                raise InvalidTimestampError(
                    f"Inspection timestamp {ts.isoformat()} exceeds allowed future clock skew "
                    f"({self.max_future_skew_s:g}s)"
                )
            if eq.last_inspected_at is not None and ts < eq.last_inspected_at:
                raise StaleInspectionError(
                    f"Inspection timestamp {ts.isoformat()} precedes last_inspected_at "
                    f"({eq.last_inspected_at.isoformat()})"
                )
            if active_alert is not None and ts < active_alert.created_at:
                raise StaleInspectionError(
                    f"Inspection timestamp {ts.isoformat()} precedes active alert timestamp "
                    f"({active_alert.created_at.isoformat()})"
                )
            return ts

        floor_candidates = [now_utc]
        if eq.last_inspected_at is not None:
            floor_candidates.append(eq.last_inspected_at)
        if active_alert is not None:
            floor_candidates.append(active_alert.created_at)
        return max(floor_candidates)

    def _classify_verification(
        self,
        eq: EquipmentRecord,
        camera_temp_c: float,
        ts: datetime,
    ) -> tuple[
        str,
        str,
        str,
        float,
        tuple[float, float, float],
        float,
        bool,
        bool,
        datetime | None,
        str | None,
    ]:
        delta_c = camera_temp_c - eq.baseline_temp_c
        if delta_c >= self.cfg.confirm_c:
            return (
                f"Close camera reading {delta_c:+.1f} °C >= confirm {self.cfg.confirm_c:.1f} °C",
                "confirmed",
                "confirmed",
                0.0,
                (eq.bayes_p_nom, eq.bayes_p_glitch, eq.bayes_p_fault),
                eq.bayes_mu_fault,
                False,
                eq.alert_armed,
                None,
                "confirmed",
            )
        if delta_c >= self.cfg.watch_c:
            return (
                f"Inconclusive reading {delta_c:+.1f} °C (>= watch {self.cfg.watch_c:.1f} °C); continue watching",
                "watch",
                eq.status,
                eq.cusum_score,
                (eq.bayes_p_nom, eq.bayes_p_glitch, eq.bayes_p_fault),
                eq.bayes_mu_fault,
                eq.alert_active,
                eq.alert_armed,
                eq.rearm_after,
                None,
            )
        if eq.alert_active:
            return (
                f"Close camera reading {delta_c:+.1f} °C < watch {self.cfg.watch_c:.1f} °C; false alarm cleared",
                "false_alarm",
                "nominal",
                0.0,
                (0.995, 0.004, 0.001),
                1.2,
                False,
                False,
                ts + timedelta(seconds=self.cfg.rearm_after_s),
                "resolved",
            )
        return (
            f"Routine check {delta_c:+.1f} °C, equipment nominal",
            "nominal",
            "confirmed" if eq.status == "confirmed" else "nominal",
            eq.cusum_score,
            (eq.bayes_p_nom, eq.bayes_p_glitch, eq.bayes_p_fault),
            eq.bayes_mu_fault,
            False,
            eq.alert_armed,
            eq.rearm_after,
            None,
        )

    def replay_scenario(
        self,
        service: MonitoringService | None = None,
        *,
        policy: Policy | str | None = None,
        seed: int = 3,
        fault_bay_id: str | None = "B5",
        max_ticks: int = 120,
    ) -> ReplaySummary:
        """Run an isolated in-memory simulation replay without modifying operational storage."""
        resolved_policy = (
            Policy(policy) if policy is not None else (service.policy if service is not None else self.policy)
        )
        resolved_config = service.cfg if service is not None else self.cfg
        return run_isolated_replay(
            policy=resolved_policy,
            config=resolved_config,
            seed=seed,
            fault_bay_id=fault_bay_id,
            max_ticks=max_ticks,
        )

    # -- planning helpers ---------------------------------------------------------------
    def _reference_time(self, equipment: list[EquipmentRecord], explicit_now: datetime | None) -> datetime:
        if explicit_now is not None:
            return _to_utc(explicit_now)
        timestamps = [_to_utc(self._clock())]
        for eq in equipment:
            for ts in (eq.last_reading_at, eq.last_inspected_at):
                if ts is not None:
                    timestamps.append(ts)
        return max(timestamps)

    def _distance(self, eq: EquipmentRecord, robot_x_m: float, robot_y_m: float) -> float:
        return math.hypot(eq.x_m - robot_x_m, eq.y_m - robot_y_m)

    def _staleness(self, eq: EquipmentRecord, ref_ts: datetime) -> float:
        if eq.last_inspected_at is None:
            return 1.0
        unseen_s = max(0.0, (ref_ts - eq.last_inspected_at).total_seconds())
        return _clip(unseen_s / self.max_unseen_s)

    def _decide(
        self,
        equipment: list[EquipmentRecord],
        policy: Policy,
        robot_x_m: float,
        robot_y_m: float,
        ref_ts: datetime,
    ) -> InspectionDecision:
        if policy is Policy.FIXED:
            return self._patrol_decision(equipment, policy, robot_x_m, robot_y_m)

        alerts = [eq for eq in equipment if eq.alert_active and eq.status != "confirmed"]
        if policy is Policy.GREEDY_EWMA:
            if alerts:
                return self._greedy_alert_decision(alerts, policy, robot_x_m, robot_y_m)
            return self._patrol_decision(equipment, policy, robot_x_m, robot_y_m)

        if alerts:
            if policy is Policy.BAYESIAN:
                return self._bayesian_alert_decision(alerts, policy, robot_x_m, robot_y_m, ref_ts)
            return self._linear_alert_decision(alerts, policy, robot_x_m, robot_y_m, ref_ts)

        limit = self.cfg.guardrail_fraction * self.max_unseen_s
        overdue: list[tuple[EquipmentRecord, float]] = []
        for eq in equipment:
            if eq.last_inspected_at is not None:
                unseen_s = (ref_ts - eq.last_inspected_at).total_seconds()
                if unseen_s >= limit:
                    overdue.append((eq, unseen_s))
        if overdue:
            chosen, unseen_s = min(overdue, key=lambda item: (-item[1], item[0].bay_id))
            dist = self._distance(chosen, robot_x_m, robot_y_m)
            return InspectionDecision(
                bay_id=chosen.bay_id,
                bay_label=chosen.label,
                mode="guardrail",
                policy=policy,
                priority_score=round(unseen_s / self.max_unseen_s, 3),
                distance_m=round(dist, 2),
                estimated_travel_s=round(dist / self.robot_speed_m_s, 2),
                close_inspection=False,
                reason=f"Coverage guardrail: {chosen.label} unseen {unseen_s:.0f}s",
                scores={chosen.bay_id: round(unseen_s, 2)},
            )

        return self._patrol_decision(equipment, policy, robot_x_m, robot_y_m)

    def _patrol_decision(
        self,
        equipment: list[EquipmentRecord],
        policy: Policy,
        robot_x_m: float,
        robot_y_m: float,
    ) -> InspectionDecision:
        chosen = min(
            equipment,
            key=lambda eq: (
                0 if eq.last_inspected_at is None else 1,
                eq.last_inspected_at or datetime.min.replace(tzinfo=UTC),
                eq.bay_id,
            ),
        )
        dist = self._distance(chosen, robot_x_m, robot_y_m)
        return InspectionDecision(
            bay_id=chosen.bay_id,
            bay_label=chosen.label,
            mode="patrol",
            policy=policy,
            priority_score=0.0,
            distance_m=round(dist, 2),
            estimated_travel_s=round(dist / self.robot_speed_m_s, 2),
            close_inspection=False,
            reason=f"Patrol route: next stop {chosen.label}",
            scores={},
        )

    def _greedy_alert_decision(
        self,
        alerts: list[EquipmentRecord],
        policy: Policy,
        robot_x_m: float,
        robot_y_m: float,
    ) -> InspectionDecision:
        dists = {eq.bay_id: self._distance(eq, robot_x_m, robot_y_m) for eq in alerts}
        scores = {eq.bay_id: round(1.0 / max(dists[eq.bay_id], 0.5), 3) for eq in alerts}
        chosen = min(alerts, key=lambda eq: (dists[eq.bay_id], eq.bay_id))
        dist = dists[chosen.bay_id]
        return InspectionDecision(
            bay_id=chosen.bay_id,
            bay_label=chosen.label,
            mode="alert",
            policy=policy,
            priority_score=scores[chosen.bay_id],
            distance_m=round(dist, 2),
            estimated_travel_s=round(dist / self.robot_speed_m_s, 2),
            close_inspection=True,
            reason=f"Greedy nearest EWMA alert: {chosen.label} ({dist:.1f} m)",
            scores=scores,
        )

    def _bayesian_alert_decision(
        self,
        alerts: list[EquipmentRecord],
        policy: Policy,
        robot_x_m: float,
        robot_y_m: float,
        ref_ts: datetime,
    ) -> InspectionDecision:
        scores: dict[str, float] = {}
        dists: dict[str, float] = {}
        for eq in alerts:
            dist = self._distance(eq, robot_x_m, robot_y_m)
            dists[eq.bay_id] = dist
            time_cost = dist / self.robot_speed_m_s + self.close_inspection_s
            stale = self._staleness(eq, ref_ts)
            rate = (eq.bayes_p_fault * max(0.5, eq.ewma_delta_c)) / max(time_cost, 1.0)
            scores[eq.bay_id] = round(rate + 0.15 * stale, 3)
        chosen = min(alerts, key=lambda eq: (-scores[eq.bay_id], eq.bay_id))
        dist = dists[chosen.bay_id]
        return InspectionDecision(
            bay_id=chosen.bay_id,
            bay_label=chosen.label,
            mode="alert",
            policy=policy,
            priority_score=scores[chosen.bay_id],
            distance_m=round(dist, 2),
            estimated_travel_s=round(dist / self.robot_speed_m_s, 2),
            close_inspection=True,
            reason=f"Bayes rate {scores[chosen.bay_id]:.2f} (P(fault)={chosen.bayes_p_fault:.2f}, dist={dist:.1f} m)",
            scores=scores,
        )

    def _linear_alert_decision(
        self,
        alerts: list[EquipmentRecord],
        policy: Policy,
        robot_x_m: float,
        robot_y_m: float,
        ref_ts: datetime,
    ) -> InspectionDecision:
        c = self.cfg
        scores: dict[str, float] = {}
        dists: dict[str, float] = {}
        terms: dict[str, tuple[float, float, float]] = {}
        for eq in alerts:
            dist = self._distance(eq, robot_x_m, robot_y_m)
            dists[eq.bay_id] = dist
            if policy is Policy.CUSUM:
                thermal = _clip(eq.cusum_score / (2.0 * c.cusum_threshold_c))
            else:
                thermal = _clip(eq.ewma_delta_c / c.thermal_scale_c)
            stale = self._staleness(eq, ref_ts)
            travel = _clip(dist / c.travel_scale_m)
            scores[eq.bay_id] = round(
                c.weight_thermal * thermal + c.weight_staleness * stale - c.weight_travel * travel,
                3,
            )
            terms[eq.bay_id] = (thermal, stale, travel)
        chosen = min(alerts, key=lambda eq: (-scores[eq.bay_id], eq.bay_id))
        th, st, tr = terms[chosen.bay_id]
        dist = dists[chosen.bay_id]
        prefix = "CUSUM" if policy is Policy.CUSUM else "Risk"
        reason = (
            f"{prefix} {scores[chosen.bay_id]:.2f} = {c.weight_thermal:.1f}·thermal {th:.2f} "
            f"+ {c.weight_staleness:.1f}·staleness {st:.2f} − {c.weight_travel:.1f}·travel {tr:.2f}"
        )
        return InspectionDecision(
            bay_id=chosen.bay_id,
            bay_label=chosen.label,
            mode="alert",
            policy=policy,
            priority_score=scores[chosen.bay_id],
            distance_m=round(dist, 2),
            estimated_travel_s=round(dist / self.robot_speed_m_s, 2),
            close_inspection=True,
            reason=reason,
            scores=scores,
        )
