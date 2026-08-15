"""Small, dependency-free simulation and explainable patrol policy.

The simulation deliberately models only the decision under review: should a
robot continue its fixed patrol, or interrupt it for a close inspection when
line telemetry indicates a rising battery-module temperature?
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import math
import random
from typing import Any, Literal

Policy = Literal["fixed", "adaptive"]


@dataclass(frozen=True)
class Bay:
    id: str
    label: str
    x: float
    y: float


@dataclass(frozen=True)
class Scenario:
    seed: int = 17
    fault_bay_id: str = "B5"
    fault_onset_s: float = 8.0
    ambient_temp_c: float = 28.0
    fault_rise_c_min: float = 14.0
    sensor_noise_c: float = 0.12
    robot_speed_m_s: float = 1.0
    scan_duration_s: float = 4.0
    close_inspection_s: float = 6.0
    max_unseen_s: float = 60.0
    max_leg_distance_m: float = 7.0
    initial_battery_pct: float = 96.0
    bays: tuple[Bay, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.bays:
            raise ValueError("scenario must contain at least one bay")
        if len({bay.id for bay in self.bays}) != len(self.bays):
            raise ValueError("bay ids must be unique")
        if self.fault_bay_id not in {bay.id for bay in self.bays}:
            raise ValueError(f"unknown fault bay: {self.fault_bay_id}")
        if self.robot_speed_m_s <= 0 or self.max_unseen_s <= 0 or self.max_leg_distance_m <= 0:
            raise ValueError("speed, max unseen, and max leg distance must be positive")
        if self.scan_duration_s <= 0 or self.close_inspection_s <= 0:
            raise ValueError("inspection durations must be positive")
        if self.sensor_noise_c < 0 or self.fault_rise_c_min <= 0:
            raise ValueError("noise cannot be negative and fault rise must be positive")
        if not 0 <= self.initial_battery_pct <= 100:
            raise ValueError("initial battery must be between 0 and 100")


# Friendly name for callers that think of these values as simulation config.
SimulationConfig = Scenario


def default_scenario(seed: int = 17, **overrides: Any) -> Scenario:
    """Return the compact six-bay pilot-line scenario."""
    values: dict[str, Any] = {
        "seed": seed,
        "bays": (
            Bay("B1", "Bay 01", 2.0, 1.0),
            Bay("B2", "Bay 02", 5.0, 1.0),
            Bay("B3", "Bay 03", 8.0, 1.0),
            Bay("B4", "Bay 04", 8.0, 5.0),
            Bay("B5", "Bay 05", 5.0, 5.0),
            Bay("B6", "Bay 06", 2.0, 5.0),
        ),
    }
    values.update(overrides)
    return Scenario(**values)


@dataclass(frozen=True)
class MissionResult:
    policy: Policy
    seed: int
    fault_bay_id: str
    events: tuple[dict[str, Any], ...]
    observations: tuple[dict[str, Any], ...]
    planner_decisions: tuple[dict[str, Any], ...]
    bays: tuple[dict[str, Any], ...]
    kpis: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComparisonResult:
    fixed: MissionResult
    adaptive: MissionResult

    @property
    def improvement_pct(self) -> float:
        fixed = self.fixed.kpis["time_to_confirm_s"]
        adaptive = self.adaptive.kpis["time_to_confirm_s"]
        if fixed is None or adaptive is None or fixed == 0:
            return 0.0
        return round(100.0 * (fixed - adaptive) / fixed, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixed": self.fixed.to_dict(),
            "adaptive": self.adaptive.to_dict(),
            "improvement": {
                "time_to_confirm_pct": self.improvement_pct,
                "time_saved_s": round(
                    self.fixed.kpis["time_to_confirm_s"]
                    - self.adaptive.kpis["time_to_confirm_s"],
                    2,
                ),
            },
        }


class Experiment:
    """A single fixed or adaptive patrol, advanced one inspection at a time."""

    WATCH_SCORE = 0.62
    CONFIRM_SCORE = 0.76
    ALERT_RISE_RATE_C_MIN = 2.0
    ALERT_TEMP_DELTA_C = 2.0

    def __init__(self, scenario: Scenario | None = None, policy: Policy = "adaptive"):
        if policy not in ("fixed", "adaptive"):
            raise ValueError("policy must be 'fixed' or 'adaptive'")
        self.scenario = scenario or default_scenario()
        self.policy = policy
        self.time_s = 0.0
        self.position = (0.0, 3.0)
        self.distance_m = 0.0
        self.battery_pct = self.scenario.initial_battery_pct
        self.current_bay_id: str | None = None
        self._unvisited = [bay.id for bay in self.scenario.bays]
        self._last_seen = {bay.id: 0.0 for bay in self.scenario.bays}
        self._latest: dict[str, dict[str, Any]] = {}
        self._close_queue: list[str] = []
        self._pending_close: str | None = None
        self._active_alerts: dict[str, dict[str, Any]] = {}
        self._confirmed_bays: set[str] = set()
        self._events: list[dict[str, Any]] = []
        self._observations: list[dict[str, Any]] = []
        self._decisions: list[dict[str, Any]] = []
        self._confirmed_at_s: float | None = None
        self._done = False
        self._emit("mission_started", message=f"{policy.title()} patrol started")

    @property
    def done(self) -> bool:
        return self._done

    @property
    def result(self) -> MissionResult | None:
        if not self.done:
            return None
        return MissionResult(
            policy=self.policy,
            seed=self.scenario.seed,
            fault_bay_id=self.scenario.fault_bay_id,
            events=tuple(self._events),
            observations=tuple(self._observations),
            planner_decisions=tuple(self._decisions),
            bays=tuple(self._bay_snapshots()),
            kpis=self._kpis(),
        )

    def step(self) -> dict[str, Any]:
        """Execute one travel-and-inspect decision and return a snapshot."""
        if self.done:
            return self.snapshot()
        self._refresh_alerts(self.time_s, self.time_s)
        if self.policy == "adaptive":
            return self._step_adaptive()
        return self._step_fixed()

    def _step_fixed(self) -> dict[str, Any]:
        close = False
        if self._pending_close:
            target = self._pending_close
            self._pending_close = None
            close = True
            reason = "Immediate close inspection: thermal risk exceeded watch threshold"
        elif self._unvisited:
            target, reason, scores = self._choose_next()
            self._unvisited.remove(target)
            self._decisions.append({
                "timestamp_s": round(self.time_s, 2),
                "selected_bay_id": target,
                "reason": reason,
                "scores": scores,
            })
        elif self._close_queue:
            target = self._close_queue.pop(0)
            close = True
            reason = "Close inspection deferred until fixed patrol completed"
        else:
            self._finish()
            return self.snapshot()

        self._travel_to(target)
        self._refresh_alerts(self.time_s, self.time_s)
        self._advance_time_with_alerts(self.scenario.close_inspection_s if close else self.scenario.scan_duration_s)
        observation = self._observe(target, close)
        self._observations.append(observation)
        self._latest[target] = observation
        self._last_seen[target] = self.time_s
        self._emit(
            "hotspot_confirmed" if observation["decision"] == "confirmed" else "bay_scanned",
            target,
            observation["reason"],
            {"anomaly_score": observation["anomaly_score"], "close_inspection": close},
        )

        if not close and observation["anomaly_score"] >= self.WATCH_SCORE:
            if self.policy == "adaptive":
                self._pending_close = target
            elif target not in self._close_queue:
                self._close_queue.append(target)

        if not self._unvisited and not self._pending_close and not self._close_queue:
            self._finish()
        return self.snapshot()

    def _step_adaptive(self) -> dict[str, Any]:
        if self._active_alerts:
            target, reason, scores = self._choose_alert_target()
            decision_mode = "alert-response"
        elif self._unvisited:
            target = self._unvisited[0]
            reason = "Routine patrol: no active alerts"
            scores = {target: 0.0}
            decision_mode = "patrol"
        else:
            self._finish()
            return self.snapshot()

        self._decisions.append({
            "timestamp_s": round(self.time_s, 2),
            "selected_bay_id": target,
            "reason": reason,
            "scores": scores,
            "mode": decision_mode,
        })
        target = self._travel_with_interrupt(target)
        close = target in self._active_alerts
        self._advance_time_with_alerts(self.scenario.close_inspection_s if close else self.scenario.scan_duration_s)
        observation = self._observe(target, close)
        self._observations.append(observation)
        self._latest[target] = observation
        self._last_seen[target] = self.time_s
        if target in self._unvisited:
            self._unvisited.remove(target)

        if close:
            if observation["decision"] == "confirmed":
                self._confirmed_bays.add(target)
                self._emit(
                    "hotspot_confirmed",
                    target,
                    observation["reason"],
                    {"anomaly_score": observation["anomaly_score"], "close_inspection": True},
                )
                self._resolve_alert(target, "confirmed issue")
            else:
                self._emit(
                    "false_alarm",
                    target,
                    observation["reason"],
                    {"anomaly_score": observation["anomaly_score"], "close_inspection": True},
                )
                self._resolve_alert(target, "false alarm")
        else:
            self._emit(
                "bay_scanned",
                target,
                observation["reason"],
                {"anomaly_score": observation["anomaly_score"], "close_inspection": False},
            )

        if not self._unvisited and not self._active_alerts:
            self._finish()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        next_id, next_reason = self._peek_next()
        return {
            "policy": self.policy,
            "seed": self.scenario.seed,
            "done": self.done,
            "time_s": round(self.time_s, 2),
            "robot": {
                "state": "complete" if self.done else ("confirming" if self._pending_close else "inspecting"),
                "battery_pct": round(self.battery_pct, 1),
                "position": {"x": round(self.position[0], 2), "y": round(self.position[1], 2)},
                "current_bay_id": self.current_bay_id,
                "distance_m": round(self.distance_m, 2),
            },
            "bays": self._bay_snapshots(),
            "planner": {
                "next_station_id": next_id,
                "reason": next_reason,
                "policy": self.policy,
                "active_alerts": sorted(self._active_alerts),
            },
            "planner_decisions": list(self._decisions),
            "observations": list(self._observations),
            "events": list(self._events),
            "kpis": self._kpis(),
        }

    def _choose_next(self) -> tuple[str, str, dict[str, float]]:
        if self.policy == "fixed":
            target = self._unvisited[0]
            return target, "Fixed clockwise validation route", {target: 0.0}

        stale = {bay_id: self.time_s - self._last_seen[bay_id] for bay_id in self._unvisited}
        most_stale = max(stale, key=stale.get)
        if stale[most_stale] >= self.scenario.max_unseen_s:
            return most_stale, f"Coverage guardrail: unseen for {stale[most_stale]:.0f}s", {most_stale: 1.0}

        scores: dict[str, float] = {}
        terms: dict[str, tuple[float, float, float, float]] = {}
        for bay_id in self._unvisited:
            bay = self._bay(bay_id)
            distance = math.dist(self.position, (bay.x, bay.y))
            thermal, rise = self._signal(bay_id, self.time_s)
            thermal_term = min(max((thermal - self.scenario.ambient_temp_c) / 4.0, 0.0), 1.0)
            rise_term = min(max(rise / self.scenario.fault_rise_c_min, 0.0), 1.0)
            stale_term = min(stale[bay_id] / self.scenario.max_unseen_s, 1.0)
            travel_term = min(distance / self.scenario.max_leg_distance_m, 1.0)
            scores[bay_id] = round(.5 * thermal_term + .4 * rise_term + .2 * stale_term - .12 * travel_term, 3)
            terms[bay_id] = (thermal_term, rise_term, stale_term, travel_term)
        target = max(self._unvisited, key=lambda bay_id: (scores[bay_id], -self._unvisited.index(bay_id)))
        t = terms[target]
        reason = (
            f"score {scores[target]:.2f} = 0.50*thermal({t[0]:.2f}) + 0.40*rise({t[1]:.2f}) "
            f"+ 0.20*staleness({t[2]:.2f}) - 0.12*travel({t[3]:.2f})"
        )
        return target, reason, scores

    def _peek_next(self) -> tuple[str | None, str]:
        if self.policy == "adaptive" and self._active_alerts:
            target, reason, _ = self._choose_alert_target()
            return target, reason
        if self._pending_close:
            return self._pending_close, "Close inspection queued by risk threshold"
        if self._unvisited:
            if self.policy == "adaptive":
                return self._unvisited[0], "Routine patrol: no active alerts"
            target, reason, _ = self._choose_next()
            return target, reason
        if self._close_queue:
            return self._close_queue[0], "Deferred close inspection"
        return None, "Mission complete"

    def _travel_with_interrupt(self, initial_target: str) -> str:
        target = initial_target
        while True:
            bay = self._bay(target)
            leg = math.dist(self.position, (bay.x, bay.y))
            if leg <= 1e-9:
                self.current_bay_id = target
                self._emit("arrived", target, "Already at target bay")
                return target
            travel_time = leg / self.scenario.robot_speed_m_s
            next_alert = self._next_alert_in_window(self.time_s, self.time_s + travel_time)
            if next_alert:
                crossed_at = next_alert["timestamp_s"]
                elapsed = max(0.0, crossed_at - self.time_s)
                ratio = min(max(elapsed / travel_time, 0.0), 1.0)
                self._advance_leg(target, leg * ratio, ratio)
                self._activate_alert(next_alert)
                preferred_target, preferred_reason, preferred_scores = self._choose_alert_target()
                if preferred_target != target:
                    self._emit(
                        "patrol_interrupted",
                        preferred_target,
                        (
                            f"PATROL INTERRUPTED: retarget from {self._label(target)} "
                            f"to {self._label(preferred_target)} because {preferred_reason}"
                        ),
                        {"from_bay_id": target, "to_bay_id": preferred_target},
                    )
                    self._decisions.append({
                        "timestamp_s": round(self.time_s, 2),
                        "selected_bay_id": preferred_target,
                        "reason": f"PATROL INTERRUPTED: {preferred_reason}",
                        "scores": preferred_scores,
                        "mode": "alert-response",
                    })
                    target = preferred_target
                continue
            self._advance_leg(target, leg, 1.0)
            self.current_bay_id = target
            self._emit("arrived", target, f"Arrived after {leg:.1f} m travel")
            return target

    def _advance_leg(self, target: str, distance: float, ratio: float) -> None:
        bay = self._bay(target)
        start_x, start_y = self.position
        self.distance_m += distance
        self.battery_pct = max(0.0, self.battery_pct - distance * 0.16)
        total_x = bay.x - start_x
        total_y = bay.y - start_y
        self.position = (start_x + total_x * ratio, start_y + total_y * ratio)
        self.time_s += distance / self.scenario.robot_speed_m_s

    def _label(self, bay_id: str) -> str:
        return self._bay(bay_id).label

    def _warning_trigger(self, temperature: float, rise: float) -> str | None:
        if rise >= self.ALERT_RISE_RATE_C_MIN:
            return f"rise rate {rise:.1f} C/min exceeds {self.ALERT_RISE_RATE_C_MIN:.1f} C/min"
        if temperature >= self.scenario.ambient_temp_c + self.ALERT_TEMP_DELTA_C:
            threshold = self.scenario.ambient_temp_c + self.ALERT_TEMP_DELTA_C
            return f"temperature {temperature:.1f} C exceeds {threshold:.1f} C threshold"
        return None

    def _next_alert_in_window(self, start_s: float, end_s: float) -> dict[str, Any] | None:
        first: dict[str, Any] | None = None
        for bay in self.scenario.bays:
            if bay.id in self._active_alerts or bay.id in self._confirmed_bays:
                continue
            crossing = self._alert_crossing_time(bay.id, start_s, end_s)
            if crossing is None:
                continue
            if first is None or crossing["timestamp_s"] < first["timestamp_s"]:
                first = crossing
        return first

    def _alert_crossing_time(self, bay_id: str, start_s: float, end_s: float) -> dict[str, Any] | None:
        if end_s < start_s:
            return None
        start_temp, start_rise = self._signal(bay_id, start_s)
        start_trigger = self._warning_trigger(start_temp, start_rise)
        if start_trigger:
            return {
                "bay_id": bay_id,
                "timestamp_s": start_s,
                "temperature_c": round(start_temp, 2),
                "rise_rate_c_min": round(start_rise, 2),
                "trigger": start_trigger,
            }
        if bay_id != self.scenario.fault_bay_id:
            return None
        onset = self.scenario.fault_onset_s
        candidates = [onset]
        rise = self.scenario.fault_rise_c_min
        threshold_temp = self.scenario.ambient_temp_c + self.ALERT_TEMP_DELTA_C
        base_offset = self._noise(bay_id, 0) * 0.22
        temp_crossing = onset + max(0.0, (threshold_temp - (self.scenario.ambient_temp_c + base_offset)) / rise * 60.0)
        candidates.append(temp_crossing)
        for candidate in sorted(candidates):
            if not (start_s < candidate <= end_s):
                continue
            temperature, rise_rate = self._signal(bay_id, candidate)
            trigger = self._warning_trigger(temperature, rise_rate)
            if trigger:
                return {
                    "bay_id": bay_id,
                    "timestamp_s": candidate,
                    "temperature_c": round(temperature, 2),
                    "rise_rate_c_min": round(rise_rate, 2),
                    "trigger": trigger,
                }
        return None

    def _activate_alert(self, alert: dict[str, Any]) -> None:
        bay_id = alert["bay_id"]
        if bay_id in self._active_alerts:
            return
        self._active_alerts[bay_id] = alert
        self._emit(
            "bay_alert",
            bay_id,
            f"{self._label(bay_id)}: NORMAL -> ALERT ({alert['trigger']})",
            {
                "temperature_c": alert["temperature_c"],
                "rise_rate_c_min": alert["rise_rate_c_min"],
                "triggered_at_s": round(alert["timestamp_s"], 2),
            },
        )

    def _resolve_alert(self, bay_id: str, outcome: str) -> None:
        if bay_id not in self._active_alerts:
            return
        self._active_alerts.pop(bay_id, None)
        self._emit(
            "alert_resolved",
            bay_id,
            f"ALERT RESOLVED at {self._label(bay_id)}: {outcome.upper()}",
            {"outcome": outcome},
        )

    def _refresh_alerts(self, start_s: float, end_s: float) -> None:
        while True:
            alert = self._next_alert_in_window(start_s, end_s)
            if not alert:
                return
            self._activate_alert(alert)
            start_s = alert["timestamp_s"]

    def _advance_time_with_alerts(self, duration_s: float) -> None:
        if duration_s <= 0:
            return
        start = self.time_s
        end = start + duration_s
        cursor = start
        while True:
            alert = self._next_alert_in_window(cursor, end)
            if not alert:
                break
            cursor = alert["timestamp_s"]
            self.time_s = cursor
            self._activate_alert(alert)
        self.time_s = end

    def _choose_alert_target(self) -> tuple[str, str, dict[str, float]]:
        scores: dict[str, float] = {}
        terms: dict[str, tuple[float, float, float, float]] = {}
        for bay_id in self._active_alerts:
            bay = self._bay(bay_id)
            distance = math.dist(self.position, (bay.x, bay.y))
            thermal, rise = self._signal(bay_id, self.time_s)
            thermal_term = min(max((thermal - self.scenario.ambient_temp_c) / 4.0, 0.0), 1.0)
            rise_term = min(max(rise / self.scenario.fault_rise_c_min, 0.0), 1.0)
            stale_term = min((self.time_s - self._last_seen[bay_id]) / self.scenario.max_unseen_s, 1.0)
            travel_term = min(distance / self.scenario.max_leg_distance_m, 1.0)
            scores[bay_id] = round(0.5 * thermal_term + 0.4 * rise_term + 0.2 * stale_term - 0.12 * travel_term, 3)
            terms[bay_id] = (thermal_term, rise_term, stale_term, travel_term)
        target = max(self._active_alerts, key=lambda bay_id: (scores[bay_id], -self._active_bay_order(bay_id)))
        thermal_term, rise_term, stale_term, travel_term = terms[target]
        reason = (
            f"Active alerts only: {self._label(target)} selected, score {scores[target]:.2f} = "
            f"0.50*thermal({thermal_term:.2f}) + 0.40*rise({rise_term:.2f}) "
            f"+ 0.20*staleness({stale_term:.2f}) - 0.12*travel({travel_term:.2f})"
        )
        return target, reason, scores

    def _active_bay_order(self, bay_id: str) -> int:
        ids = [bay.id for bay in self.scenario.bays]
        return ids.index(bay_id)

    def _travel_to(self, bay_id: str) -> None:
        bay = self._bay(bay_id)
        leg = math.dist(self.position, (bay.x, bay.y))
        self.distance_m += leg
        self.time_s += leg / self.scenario.robot_speed_m_s
        self.battery_pct = max(0.0, self.battery_pct - leg * 0.16)
        self.position = (bay.x, bay.y)
        self.current_bay_id = bay_id
        self._emit("arrived", bay_id, f"Arrived after {leg:.1f} m travel")

    def _observe(self, bay_id: str, close: bool) -> dict[str, Any]:
        first_visit = bay_id not in self._latest
        temperature, rise = self._signal(bay_id, self.time_s)
        noise_scale = self.scenario.sensor_noise_c * (0.35 if close else 1.0)
        temperature += self._noise(bay_id, 2 if close else 1) * noise_scale
        thermal_term = min(max((temperature - self.scenario.ambient_temp_c) / 4.0, 0.0), 1.0)
        rise_term = min(max(rise / self.scenario.fault_rise_c_min, 0.0), 1.0)
        score = round(.55 * thermal_term + .45 * rise_term, 3)
        confirmed = close and score >= self.CONFIRM_SCORE
        if confirmed and self._confirmed_at_s is None:
            self._confirmed_at_s = self.time_s
        if close:
            decision = "confirmed" if confirmed else "false_alarm"
        else:
            decision = "watch" if score >= self.WATCH_SCORE else "nominal"
        reason = (
            f"Confirmed thermal ramp: {temperature:.1f} C, {rise:.1f} C/min"
            if confirmed else
            f"False alarm: close inspection score {score:.2f} < {self.CONFIRM_SCORE:.2f}"
            if close else
            f"Second viewpoint required: score {score:.2f} >= {self.WATCH_SCORE:.2f}"
            if decision == "watch" else
            f"Within envelope: score {score:.2f} < {self.WATCH_SCORE:.2f}"
        )
        return {
            "bay_id": bay_id,
            "timestamp_s": round(self.time_s, 2),
            "pass_index": 2 if close and not first_visit else 1,
            "temperature_c": round(temperature, 2),
            "rise_rate_c_min": round(rise, 2),
            "anomaly_score": score,
            "decision": decision,
            "reason": reason,
        }

    def _signal(self, bay_id: str, timestamp_s: float) -> tuple[float, float]:
        base_offset = self._noise(bay_id, 0) * 0.22
        if bay_id == self.scenario.fault_bay_id and timestamp_s >= self.scenario.fault_onset_s:
            elapsed_min = (timestamp_s - self.scenario.fault_onset_s) / 60.0
            rise = self.scenario.fault_rise_c_min
            return self.scenario.ambient_temp_c + base_offset + min(rise * elapsed_min, 12.0), rise
        return self.scenario.ambient_temp_c + base_offset, 0.0

    def _noise(self, bay_id: str, pass_index: int) -> float:
        digest = hashlib.sha256(f"{self.scenario.seed}:{bay_id}:{pass_index}".encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big")).gauss(0.0, 1.0)

    def _bay(self, bay_id: str) -> Bay:
        return next(bay for bay in self.scenario.bays if bay.id == bay_id)

    def _bay_snapshots(self) -> list[dict[str, Any]]:
        snapshots = []
        for bay in self.scenario.bays:
            latest = self._latest.get(bay.id)
            temperature, rise = self._signal(bay.id, self.time_s)
            decision = latest["decision"] if latest else "nominal"
            status = (
                "critical"
                if bay.id in self._confirmed_bays or decision == "confirmed"
                else "alert"
                if bay.id in self._active_alerts
                else "watch"
                if decision == "watch"
                else "nominal"
            )
            snapshots.append({
                "id": bay.id,
                "label": bay.label,
                "x": bay.x,
                "y": bay.y,
                "status": status,
                "temperature_c": latest["temperature_c"] if latest else round(temperature, 2),
                "rise_rate_c_min": latest["rise_rate_c_min"] if latest else round(rise, 2),
                "last_seen_s": None if not latest else round(self.time_s - self._last_seen[bay.id], 2),
                "anomaly_score": latest["anomaly_score"] if latest else 0.0,
            })
        return snapshots

    def _kpis(self) -> dict[str, Any]:
        first_pass_ids = {item["bay_id"] for item in self._observations if item["pass_index"] == 1}
        max_unseen = max((self.time_s - seen for seen in self._last_seen.values()), default=0.0)
        return {
            "time_to_confirm_s": None if self._confirmed_at_s is None else round(self._confirmed_at_s - self.scenario.fault_onset_s, 2),
            "mission_elapsed_s": round(self.time_s, 2),
            "distance_m": round(self.distance_m, 2),
            "coverage_pct": round(100.0 * len(first_pass_ids) / len(self.scenario.bays), 1),
            "bays_inspected": len(first_pass_ids),
            "reinspections": sum(item["pass_index"] == 2 for item in self._observations),
            "confirmed_hotspots": int(self._confirmed_at_s is not None),
            "max_unseen_s": round(max_unseen, 2),
            "guardrails_respected": self.distance_m >= 0 and max_unseen <= self.scenario.max_unseen_s,
        }

    def _emit(self, kind: str, bay_id: str | None = None, message: str = "", data: dict[str, Any] | None = None) -> None:
        self._events.append({
            "sequence": len(self._events) + 1,
            "timestamp_s": round(self.time_s, 2),
            "type": kind,
            "bay_id": bay_id,
            "message": message,
            "data": data or {},
        })

    def _finish(self) -> None:
        if not self._done:
            self._done = True
            self._emit("mission_completed", message="All six bays inspected")


def run_policy(policy: Policy, scenario: Scenario | None = None, *, seed: int | None = None) -> MissionResult:
    if scenario is None:
        scenario = default_scenario(17 if seed is None else seed)
    experiment = Experiment(scenario, policy)
    while not experiment.done:
        experiment.step()
    result = experiment.result
    assert result is not None
    return result


def run_comparison(scenario: Scenario | None = None, *, seed: int | None = None) -> ComparisonResult:
    scenario = scenario or default_scenario(17 if seed is None else seed)
    return ComparisonResult(
        fixed=run_policy("fixed", scenario),
        adaptive=run_policy("adaptive", scenario),
    )


def run_pair(seed: int = 17) -> dict[str, Any]:
    """Convenience API for an HTTP adapter or benchmark script."""
    return run_comparison(seed=seed).to_dict()
