"""Time-stepped patrol simulation for the fixed and risk-adaptive policies.

Both policies share one patrol route and one inspection rule set. The *only*
difference is that the adaptive policy listens to line telemetry alerts and may
interrupt its route to go and look. Any difference in the results is therefore
caused by that single decision rule.
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
    ADAPTIVE = "adaptive"


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
    """Detection thresholds and risk weights. Shared by both policies."""

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
            self.ewma[bay_id] = a * self.env.line_delta_c(bay_id, tick) + (1 - a) * self.ewma[bay_id]
            if bay_id in self.confirmed:
                continue
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

    # -- acting -------------------------------------------------------------------------
    def _act(self, tick: int) -> None:
        if self.state in (RobotState.SCANNING, RobotState.CONFIRMING):
            self.activity_ticks -= 1
            if self.activity_ticks == 0:
                self._complete_inspection(tick + 1)
            return

        t = tick * self.s.dt_s
        active = frozenset(b for b in self.ids if self.alert[b])
        replan = self.state == RobotState.IDLE or (self.policy is Policy.ADAPTIVE and active != self._planned_alerts)
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
            close = self.policy is Policy.ADAPTIVE and self.alert[bay.id]
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
        if self.policy is Policy.ADAPTIVE:
            alerts = [b for b in self.ids if self.alert[b]]
            if alerts:
                return self._plan_alert(t, alerts)
            limit = self.cfg.guardrail_fraction * self.s.max_unseen_s
            overdue = [b for b in self.ids if t - self.last_seen[b] >= limit]
            if overdue:
                bay_id = max(overdue, key=lambda b: (t - self.last_seen[b], -self.ids.index(b)))
                unseen = t - self.last_seen[bay_id]
                return _Plan(bay_id, "guardrail", f"Coverage guardrail: {self._label(bay_id)} unseen {unseen:.0f}s")
        bay_id = self.ids[self.route_index]
        return _Plan(bay_id, "patrol", f"Patrol route: next stop {self._label(bay_id)}")

    def _plan_alert(self, t: float, alerts: list[str]) -> _Plan:
        c = self.cfg
        scores: dict[str, float] = {}
        terms: dict[str, tuple[float, float, float]] = {}
        for bay_id in alerts:
            bay = self.s.bay(bay_id)
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
        reason = (
            f"Risk {scores[best]:.2f} = {c.weight_thermal:.1f}·thermal {th:.2f} "
            f"+ {c.weight_staleness:.1f}·staleness {st:.2f} − {c.weight_travel:.1f}·travel {tr:.2f}"
        )
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
    """Run both policies against one shared environment."""
    env = Environment(scenario)
    return PairResult(
        scenario=scenario,
        fixed=Simulation(env, Policy.FIXED, config).run(),
        adaptive=Simulation(env, Policy.ADAPTIVE, config).run(),
    )
