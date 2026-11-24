"""Time-stepped patrol simulation for fixed, EWMA, CUSUM, and Bayesian HMM policies.

All policies share one deterministic patrol route, sensor stream, and close-range
verification model per scenario. Any difference in outcomes comes purely from the
anomaly detector and dispatch planner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from .environment import Environment
from .model import Scenario

__all__ = [
    "BayStatus",
    "PairResult",
    "Policy",
    "PolicyConfig",
    "RobotState",
    "RunResult",
    "Simulation",
    "run_pair",
    "run_policy",
]


class Policy(StrEnum):
    FIXED = "fixed"
    GREEDY_EWMA = "greedy_ewma"
    ADAPTIVE = "adaptive"
    CUSUM = "cusum"
    BAYESIAN = "bayesian"


class RobotState(IntEnum):
    IDLE = 0
    MOVING = 1
    SCANNING = 2
    CONFIRMING = 3


class BayStatus(IntEnum):
    NOMINAL = 0
    ALERT = 1
    CONFIRMED = 2


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    """Detection thresholds and risk weights shared across patrol policies."""

    ewma_alpha: float = 0.3
    alert_on_c: float = 1.2
    alert_off_c: float = 0.6
    rearm_after_s: float = 15.0
    watch_c: float = 1.0
    confirm_c: float = 1.5
    max_dwells: int = 2
    guardrail_fraction: float = 0.8
    weight_thermal: float = 0.7
    weight_staleness: float = 0.2
    weight_travel: float = 0.1
    thermal_scale_c: float = 3.0
    travel_scale_m: float = 10.0
    cusum_drift_c: float = 0.6
    cusum_threshold_c: float = 16.0
    bayes_alert_prob: float = 0.70
    bayes_clear_prob: float = 0.25

    def __post_init__(self) -> None:
        numeric = (
            self.ewma_alpha,
            self.alert_on_c,
            self.alert_off_c,
            self.rearm_after_s,
            self.watch_c,
            self.confirm_c,
            self.guardrail_fraction,
            self.weight_thermal,
            self.weight_staleness,
            self.weight_travel,
            self.thermal_scale_c,
            self.travel_scale_m,
            self.cusum_drift_c,
            self.cusum_threshold_c,
            self.bayes_alert_prob,
            self.bayes_clear_prob,
        )
        if not all(math.isfinite(val) for val in numeric):
            raise ValueError("PolicyConfig parameters must be finite numbers")
        if not 0.0 < self.ewma_alpha <= 1.0:
            raise ValueError("ewma_alpha must be in (0, 1]")
        if self.alert_off_c >= self.alert_on_c:
            raise ValueError("alert_off_c must be strictly less than alert_on_c")
        if not 0.0 < self.watch_c <= self.confirm_c:
            raise ValueError("watch_c must be positive and at most confirm_c")
        if self.rearm_after_s < 0.0:
            raise ValueError("rearm_after_s cannot be negative")
        if not 0.0 < self.guardrail_fraction <= 1.0:
            raise ValueError("guardrail_fraction must be in (0, 1]")
        if self.cusum_threshold_c <= 0.0:
            raise ValueError("cusum_threshold_c must be positive")
        if not 0.0 <= self.bayes_clear_prob < self.bayes_alert_prob <= 1.0:
            raise ValueError("bayes probabilities must satisfy 0 <= clear < alert <= 1")
        if self.thermal_scale_c <= 0.0 or self.travel_scale_m <= 0.0:
            raise ValueError("scale parameters must be positive")


@dataclass(frozen=True, slots=True)
class RunResult:
    policy: Policy
    case_id: str
    metrics: dict[str, Any]
    events: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    frames: dict[str, list[Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.value,
            "case_id": self.case_id,
            "metrics": self.metrics,
            "events": self.events,
            "decisions": self.decisions,
            "frames": self.frames,
        }


@dataclass(frozen=True, slots=True)
class PairResult:
    scenario: Scenario
    fixed: RunResult
    adaptive: RunResult

    @property
    def time_saved_s(self) -> float | None:
        f = self.fixed.metrics["time_to_confirm_s"]
        a = self.adaptive.metrics["time_to_confirm_s"]
        return None if f is None or a is None else round(f - a, 2)


def _clip(value: float) -> float:
    return min(max(value, 0.0), 1.0)


@dataclass(slots=True)
class _Plan:
    bay_id: str
    mode: str
    reason: str
    scores: dict[str, float] = field(default_factory=dict)


class Simulation:
    """One policy executing against one environment. Call :meth:`run` once."""

    def __init__(self, env: Environment, policy: Policy, config: PolicyConfig | None = None) -> None:
        self.env = env
        self.s = env.scenario
        self.policy = Policy(policy)
        self.cfg = config or PolicyConfig()
        ids = env.bay_ids
        self.ids = ids
        self.pos: tuple[float, float] = self.s.dock
        self.state = RobotState.IDLE
        self.target: str | None = None
        self.activity_ticks = 0
        self.dwells = 0
        self.route_index = 0
        self.last_seen = dict.fromkeys(ids, 0.0)
        self.ewma = dict.fromkeys(ids, 0.0)
        self.cusum = dict.fromkeys(ids, 0.0)
        self.bayes_post: dict[str, tuple[float, float, float]] = dict.fromkeys(ids, (0.995, 0.004, 0.001))
        self.bayes_mu_fault = dict.fromkeys(ids, 1.2)
        self.alert = dict.fromkeys(ids, False)
        self.armed = dict.fromkeys(ids, True)
        self.rearm_at = dict.fromkeys(ids, 0.0)
        self.confirmed: set[str] = set()
        self._planned_alerts: frozenset[str] = frozenset()
        self.distance_m = 0.0
        self.max_unseen_s = 0.0
        self.scans = 0
        self.close_inspections = 0
        self.false_alarms = 0
        self.false_confirmations = 0
        self.line_alerts = 0
        self.retargets = 0
        self.confirmed_at_s: float | None = None
        self.events: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.frames: dict[str, list[Any]] = {
            "t": [],
            "x": [],
            "y": [],
            "state": [],
            "target": [],
            "status": [],
            "line": [],
            "distance": [],
        }
        self._done = False

    # -- public -------------------------------------------------------------------------
    def run(self) -> RunResult:
        if self._done:
            raise RuntimeError("a Simulation can only be run once")
        self._done = True
        self._emit(0.0, "mission_started", None, f"{self.policy.value.title()} patrol started from dock")
        self._record(0.0)
        dt = self.s.dt_s
        for tick in range(self.s.ticks):
            self._sense(tick, tick * dt)
            self._act(tick)
            end = (tick + 1) * dt
            gap = max(end - seen for seen in self.last_seen.values())
            self.max_unseen_s = max(self.max_unseen_s, gap)
            self._record(end)
        self._emit(self.s.horizon_s, "mission_ended", None, "Mission horizon reached")
        return RunResult(self.policy, self.s.case_id, self._metrics(), self.events, self.decisions, self.frames)

    # -- sensing ------------------------------------------------------------------------
    def _sense(self, tick: int, t: float) -> None:
        a = self.cfg.ewma_alpha
        for bay_id in self.ids:
            raw_delta = self.env.line_delta_c(bay_id, tick)
            self.ewma[bay_id] = a * raw_delta + (1 - a) * self.ewma[bay_id]
            if bay_id in self.confirmed:
                continue

            if self.policy is Policy.CUSUM:
                self._sense_cusum(bay_id, raw_delta, t)
            elif self.policy is Policy.BAYESIAN:
                self._sense_bayesian(bay_id, raw_delta, t)
            else:
                level = self.ewma[bay_id]
                if not self.armed[bay_id] and (level < self.cfg.alert_off_c or t >= self.rearm_at[bay_id]):
                    self.armed[bay_id] = True
                if not self.alert[bay_id] and self.armed[bay_id] and level >= self.cfg.alert_on_c:
                    self.alert[bay_id] = True
                    self.line_alerts += 1
                    self._emit(t, "line_alert", bay_id, f"Line telemetry +{level:.1f} °C above baseline")
                elif self.alert[bay_id] and level < self.cfg.alert_off_c:
                    self.alert[bay_id] = False
                    self._emit(t, "alert_cleared", bay_id, "Telemetry back within normal band")

    def _sense_cusum(self, bay_id: str, raw_delta: float, t: float) -> None:
        s_val = max(0.0, self.cusum[bay_id] + (raw_delta - self.cfg.cusum_drift_c))
        self.cusum[bay_id] = s_val
        clear_level = 0.25 * self.cfg.cusum_threshold_c
        if not self.armed[bay_id] and (s_val < clear_level or t >= self.rearm_at[bay_id]):
            self.armed[bay_id] = True
        if not self.alert[bay_id] and self.armed[bay_id] and s_val >= self.cfg.cusum_threshold_c:
            self.alert[bay_id] = True
            self.line_alerts += 1
            self._emit(t, "line_alert", bay_id, f"CUSUM S={s_val:.1f} °C·tick")
        elif self.alert[bay_id] and s_val < clear_level:
            self.alert[bay_id] = False
            self._emit(t, "alert_cleared", bay_id, "CUSUM back within nominal band")

    def _sense_bayesian(self, bay_id: str, raw_delta: float, t: float) -> None:
        p_nom, p_gl, p_flt = self.bayes_post[bay_id]
        # Markov transition step across {NOMINAL, GLITCH, FAULT_RAMP}
        pred_nom = (1.0 - 0.002 - 0.0005) * p_nom + (1.0 / 6.0) * p_gl + 0.005 * p_flt
        pred_gl = 0.002 * p_nom + (5.0 / 6.0) * p_gl
        pred_flt = 0.0005 * p_nom + 0.995 * p_flt

        mu_f = max(self.bayes_mu_fault[bay_id], raw_delta) if raw_delta > 3.8 else self.bayes_mu_fault[bay_id]
        inv_2var_n = -0.5 / (0.35 * 0.35)
        inv_2var_f = -0.5 / (0.65 * 0.65)
        log_n = math.log(max(pred_nom, 1e-12)) + inv_2var_n * (raw_delta * raw_delta) - math.log(0.35)
        log_g = math.log(max(pred_gl, 1e-12)) + inv_2var_n * ((raw_delta - 3.0) ** 2) - math.log(0.35)
        log_f = math.log(max(pred_flt, 1e-12)) + inv_2var_f * ((raw_delta - mu_f) ** 2) - math.log(0.65)

        m_log = max(log_n, log_g, log_f)
        un_n = math.exp(log_n - m_log)
        un_g = math.exp(log_g - m_log)
        un_f = math.exp(log_f - m_log)
        norm = un_n + un_g + un_f
        p_nom_new, p_gl_new, p_flt_new = un_n / norm, un_g / norm, un_f / norm
        self.bayes_post[bay_id] = (p_nom_new, p_gl_new, p_flt_new)

        w = 0.12 * p_flt_new
        mu_updated = (1.0 - w) * mu_f + w * max(0.8, min(15.0, raw_delta))
        if p_nom_new > 0.95:
            mu_updated = 0.9 * mu_updated + 0.1 * 1.2
        self.bayes_mu_fault[bay_id] = mu_updated

        if not self.armed[bay_id] and (p_flt_new < self.cfg.bayes_clear_prob or t >= self.rearm_at[bay_id]):
            self.armed[bay_id] = True
        if not self.alert[bay_id] and self.armed[bay_id] and p_flt_new >= self.cfg.bayes_alert_prob:
            self.alert[bay_id] = True
            self.line_alerts += 1
            self._emit(t, "line_alert", bay_id, f"Bayesian HMM P(fault)={p_flt_new:.2f}")
        elif self.alert[bay_id] and p_flt_new < self.cfg.bayes_clear_prob:
            self.alert[bay_id] = False
            self._emit(t, "alert_cleared", bay_id, "Bayesian HMM fault posterior cleared")

    # -- acting -------------------------------------------------------------------------
    def _act(self, tick: int) -> None:
        if self.state in (RobotState.SCANNING, RobotState.CONFIRMING):
            self.activity_ticks -= 1
            if self.activity_ticks == 0:
                self._complete_inspection(tick + 1)
            return

        t = tick * self.s.dt_s
        active = frozenset(b for b in self.ids if self.alert[b])
        replan = self.state == RobotState.IDLE or (self.policy is not Policy.FIXED and active != self._planned_alerts)
        if replan:
            plan = self._plan(t)
            self._planned_alerts = active
            if plan.bay_id != self.target or self.state == RobotState.IDLE:
                if self.state == RobotState.MOVING and self.target is not None:
                    self.retargets += 1
                    self._emit(
                        t,
                        "retarget",
                        plan.bay_id,
                        f"Route interrupted: {self._label(self.target)} → {self._label(plan.bay_id)}",
                    )
                self.target = plan.bay_id
                self.decisions.append(
                    {
                        "t": round(t, 2),
                        "bay": plan.bay_id,
                        "mode": plan.mode,
                        "reason": plan.reason,
                        "scores": plan.scores,
                    }
                )
            self.state = RobotState.MOVING
        self._move(t)

    def _move(self, t: float) -> None:
        assert self.target is not None
        bay = self.s.bay(self.target)
        dx, dy = bay.x - self.pos[0], bay.y - self.pos[1]
        remaining = math.hypot(dx, dy)
        step = self.s.robot_speed_m_s * self.s.dt_s
        if remaining <= step + 1e-9:
            self.distance_m += remaining
            self.pos = (bay.x, bay.y)
            self._emit(t, "arrived", bay.id, f"Arrived at {bay.label}")
            close = self.policy is not Policy.FIXED and self.alert[bay.id]
            self._start_inspection(close)
        else:
            ratio = step / remaining
            self.pos = (self.pos[0] + dx * ratio, self.pos[1] + dy * ratio)
            self.distance_m += step

    def _start_inspection(self, close: bool) -> None:
        duration = self.s.close_inspection_s if close else self.s.scan_s
        if self.state not in (RobotState.SCANNING, RobotState.CONFIRMING):
            self.dwells = 0
        self.state = RobotState.CONFIRMING if close else RobotState.SCANNING
        self.activity_ticks = round(duration / self.s.dt_s)

    def _complete_inspection(self, end_tick: int) -> None:
        assert self.target is not None
        bay_id = self.target
        t = end_tick * self.s.dt_s
        close = self.state == RobotState.CONFIRMING
        reading = self.env.camera_delta_c(bay_id, end_tick, close=close)
        self.last_seen[bay_id] = t
        if bay_id == self.ids[self.route_index]:
            self.route_index = (self.route_index + 1) % len(self.ids)

        if not close:
            self.scans += 1
            if reading >= self.cfg.watch_c and bay_id not in self.confirmed:
                self._emit(
                    t,
                    "second_look",
                    bay_id,
                    f"Scan +{reading:.1f} °C ≥ watch {self.cfg.watch_c:.1f} °C, close inspection",
                )
                self._start_inspection(close=True)
                return
            self._emit(t, "scanned", bay_id, f"Scan +{reading:.1f} °C, nominal")
        else:
            self.close_inspections += 1
            if reading >= self.cfg.confirm_c:
                newly = bay_id not in self.confirmed
                self.confirmed.add(bay_id)
                self.alert[bay_id] = False
                self.cusum[bay_id] = 0.0
                if newly:
                    if bay_id == self.s.fault_bay_id and self.confirmed_at_s is None:
                        self.confirmed_at_s = t
                    elif bay_id != self.s.fault_bay_id:
                        self.false_confirmations += 1
                    self._emit(t, "confirmed", bay_id, f"Hotspot confirmed: +{reading:.1f} °C at close range")
            elif reading >= self.cfg.watch_c and self.dwells < self.cfg.max_dwells:
                self.dwells += 1
                self._emit(t, "dwell", bay_id, f"Inconclusive +{reading:.1f} °C, holding position to re-measure")
                self._start_inspection(close=True)
                return
            else:
                self.false_alarms += 1
                self.cusum[bay_id] = 0.0
                self.bayes_post[bay_id] = (0.995, 0.004, 0.001)
                self.bayes_mu_fault[bay_id] = 1.2
                if self.alert[bay_id]:
                    self.alert[bay_id] = False
                    self.armed[bay_id] = False
                    self.rearm_at[bay_id] = t + self.cfg.rearm_after_s
                self._emit(
                    t,
                    "false_alarm",
                    bay_id,
                    f"Close look +{reading:.1f} °C < confirm {self.cfg.confirm_c:.1f} °C",
                )
        self.state = RobotState.IDLE

    # -- planning -------------------------------------------------------------------------
    def _plan(self, t: float) -> _Plan:
        if self.policy is Policy.FIXED:
            bay_id = self.ids[self.route_index]
            return _Plan(bay_id, "patrol", f"Patrol route: next stop {self._label(bay_id)}")

        alerts = [b for b in self.ids if self.alert[b]]
        if self.policy is Policy.GREEDY_EWMA:
            if alerts:
                return self._plan_greedy_nearest(alerts)
            bay_id = self.ids[self.route_index]
            return _Plan(bay_id, "patrol", f"Patrol route: next stop {self._label(bay_id)}")

        if alerts:
            if self.policy is Policy.BAYESIAN:
                return self._plan_bayesian_alert(t, alerts)
            return self._plan_alert(t, alerts)

        limit = self.cfg.guardrail_fraction * self.s.max_unseen_s
        overdue = [b for b in self.ids if t - self.last_seen[b] >= limit]
        if overdue:
            bay_id = max(overdue, key=lambda b: (t - self.last_seen[b], -self.ids.index(b)))
            unseen = t - self.last_seen[bay_id]
            return _Plan(bay_id, "guardrail", f"Coverage guardrail: {self._label(bay_id)} unseen {unseen:.0f}s")

        bay_id = self.ids[self.route_index]
        return _Plan(bay_id, "patrol", f"Patrol route: next stop {self._label(bay_id)}")

    def _plan_greedy_nearest(self, alerts: list[str]) -> _Plan:
        dists: dict[str, float] = {}
        scores: dict[str, float] = {}
        for bay_id in alerts:
            bay = self.s.bay(bay_id)
            dist = math.hypot(bay.x - self.pos[0], bay.y - self.pos[1])
            dists[bay_id] = dist
            scores[bay_id] = round(1.0 / max(dist, 0.5), 3)
        best = min(alerts, key=lambda b: (dists[b], self.ids.index(b)))
        return _Plan(best, "alert", f"Greedy nearest EWMA alert: {self._label(best)} ({dists[best]:.1f} m)", scores)

    def _plan_alert(self, t: float, alerts: list[str]) -> _Plan:
        c = self.cfg
        scores: dict[str, float] = {}
        terms: dict[str, tuple[float, float, float]] = {}
        for bay_id in alerts:
            bay = self.s.bay(bay_id)
            if self.policy is Policy.CUSUM:
                thermal = _clip(self.cusum[bay_id] / (2.0 * c.cusum_threshold_c))
            else:
                thermal = _clip(self.ewma[bay_id] / c.thermal_scale_c)
            stale = _clip((t - self.last_seen[bay_id]) / self.s.max_unseen_s)
            travel = _clip(math.hypot(bay.x - self.pos[0], bay.y - self.pos[1]) / c.travel_scale_m)
            scores[bay_id] = round(
                c.weight_thermal * thermal + c.weight_staleness * stale - c.weight_travel * travel,
                3,
            )
            terms[bay_id] = (thermal, stale, travel)
        best = max(alerts, key=lambda b: (scores[b], -self.ids.index(b)))
        th, st, tr = terms[best]
        prefix = "CUSUM" if self.policy is Policy.CUSUM else "Risk"
        reason = (
            f"{prefix} {scores[best]:.2f} = {c.weight_thermal:.1f}·thermal {th:.2f} "
            f"+ {c.weight_staleness:.1f}·staleness {st:.2f} − {c.weight_travel:.1f}·travel {tr:.2f}"
        )
        return _Plan(best, "alert", reason, scores)

    def _plan_bayesian_alert(self, t: float, alerts: list[str]) -> _Plan:
        scores: dict[str, float] = {}
        details: dict[str, tuple[float, float]] = {}
        for bay_id in alerts:
            bay = self.s.bay(bay_id)
            p_flt = self.bayes_post[bay_id][2]
            dist = math.hypot(bay.x - self.pos[0], bay.y - self.pos[1])
            time_cost = dist / self.s.robot_speed_m_s + self.s.close_inspection_s
            stale = _clip((t - self.last_seen[bay_id]) / self.s.max_unseen_s)
            rate = (p_flt * max(0.5, self.ewma[bay_id])) / max(time_cost, 1.0)
            scores[bay_id] = round(rate + 0.15 * stale, 3)
            details[bay_id] = (p_flt, dist)
        best = max(alerts, key=lambda b: (scores[b], -self.ids.index(b)))
        p_b, dist_b = details[best]
        reason = f"Bayes rate {scores[best]:.2f} (P(fault)={p_b:.2f}, dist={dist_b:.1f} m)"
        return _Plan(best, "alert", reason, scores)

    # -- bookkeeping ----------------------------------------------------------------------
    def _label(self, bay_id: str) -> str:
        return self.s.bay(bay_id).label

    def _emit(self, t: float, kind: str, bay_id: str | None, message: str) -> None:
        self.events.append({"t": round(t, 2), "type": kind, "bay": bay_id, "message": message})

    def _record(self, t: float) -> None:
        f = self.frames
        f["t"].append(round(t, 2))
        f["x"].append(round(self.pos[0], 3))
        f["y"].append(round(self.pos[1], 3))
        f["state"].append(int(self.state))
        f["target"].append(self.ids.index(self.target) if self.target else -1)
        f["status"].append(
            [
                int(
                    BayStatus.CONFIRMED
                    if b in self.confirmed
                    else BayStatus.ALERT
                    if self.alert[b]
                    else BayStatus.NOMINAL
                )
                for b in self.ids
            ]
        )
        f["line"].append([round(self.ewma[b], 2) for b in self.ids])
        f["distance"].append(round(self.distance_m, 2))

    def _metrics(self) -> dict[str, Any]:
        s = self.s
        ttc = None if self.confirmed_at_s is None else round(self.confirmed_at_s - s.fault_onset_s, 2)
        return {
            "fault_present": s.fault_bay_id is not None,
            "detected": self.confirmed_at_s is not None,
            "time_to_confirm_s": ttc,
            "distance_m": round(self.distance_m, 2),
            "scans": self.scans,
            "close_inspections": self.close_inspections,
            "false_alarms": self.false_alarms,
            "false_confirmations": self.false_confirmations,
            "line_alerts": self.line_alerts,
            "retargets": self.retargets,
            "max_unseen_s": round(self.max_unseen_s, 2),
            "guardrail_ok": self.max_unseen_s <= s.max_unseen_s,
        }


def run_policy(
    scenario: Scenario, policy: Policy | str, config: PolicyConfig | None = None, env: Environment | None = None
) -> RunResult:
    return Simulation(env or Environment(scenario), Policy(policy), config).run()


def run_pair(scenario: Scenario, config: PolicyConfig | None = None) -> PairResult:
    """Run both fixed and risk-adaptive policies against one shared environment."""
    env = Environment(scenario)
    return PairResult(
        scenario=scenario,
        fixed=Simulation(env, Policy.FIXED, config).run(),
        adaptive=Simulation(env, Policy.ADAPTIVE, config).run(),
    )
