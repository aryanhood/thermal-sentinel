"""Trend monitoring and sequential anomaly detection service backed by SQLite."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .simulation import Policy, PolicyConfig
from .storage import (
    AlertRecord,
    ConflictingReadingError,
    EquipmentRecord,
    InvalidTimestampError,
    MonitoringStore,
    SensorMismatchError,
    SensorReadingRecord,
)

__all__ = [
    "IngestionResult",
    "MonitoringService",
    "ReadingInput",
    "update_bayesian_hmm",
    "update_cusum",
    "update_ewma",
]


@dataclass(frozen=True, slots=True)
class ReadingInput:
    """One validated sensor measurement waiting to be ingested."""

    bay_id: str
    temperature_c: float
    sensor_id: str | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Result returned after processing one sensor reading."""

    reading: SensorReadingRecord
    equipment: EquipmentRecord
    alert: AlertRecord | None
    alert_event: str | None
    applied_to_detector: bool = True
    out_of_order: bool = False
    duplicate: bool = False


def _require_finite(*values: float) -> None:
    if not all(math.isfinite(val) for val in values):
        raise ValueError("Numeric monitoring inputs must be finite")


def update_ewma(prev_ewma: float, delta_c: float, alpha: float) -> float:
    """Update exponentially weighted moving average of temperature delta."""
    _require_finite(prev_ewma, delta_c, alpha)
    return alpha * delta_c + (1.0 - alpha) * prev_ewma


def update_cusum(prev_cusum: float, delta_c: float, drift_c: float) -> float:
    """Update Page's one-sided CUSUM statistic."""
    _require_finite(prev_cusum, delta_c, drift_c)
    return max(0.0, prev_cusum + (delta_c - drift_c))


def update_bayesian_hmm(
    posterior: tuple[float, float, float],
    mu_fault: float,
    delta_c: float,
) -> tuple[tuple[float, float, float], float]:
    """Advance the 3-state Bayesian HMM ({NOMINAL, GLITCH, FAULT_RAMP}) by one step."""
    p_nom, p_gl, p_flt = posterior
    _require_finite(p_nom, p_gl, p_flt, mu_fault, delta_c)
    pred_nom = (1.0 - 0.002 - 0.0005) * p_nom + (1.0 / 6.0) * p_gl + 0.005 * p_flt
    pred_gl = 0.002 * p_nom + (5.0 / 6.0) * p_gl
    pred_flt = 0.0005 * p_nom + 0.995 * p_flt

    mu_f = max(mu_fault, delta_c) if delta_c > 3.8 else mu_fault
    inv_2var_n = -0.5 / (0.35 * 0.35)
    inv_2var_f = -0.5 / (0.65 * 0.65)
    log_n = math.log(max(pred_nom, 1e-12)) + inv_2var_n * (delta_c * delta_c) - math.log(0.35)
    log_g = math.log(max(pred_gl, 1e-12)) + inv_2var_n * ((delta_c - 3.0) ** 2) - math.log(0.35)
    log_f = math.log(max(pred_flt, 1e-12)) + inv_2var_f * ((delta_c - mu_f) ** 2) - math.log(0.65)

    m_log = max(log_n, log_g, log_f)
    un_n = math.exp(log_n - m_log)
    un_g = math.exp(log_g - m_log)
    un_f = math.exp(log_f - m_log)
    norm = un_n + un_g + un_f
    p_nom_new, p_gl_new, p_flt_new = un_n / norm, un_g / norm, un_f / norm

    weight = 0.12 * p_flt_new
    mu_updated = (1.0 - weight) * mu_f + weight * max(0.8, min(15.0, delta_c))
    if p_nom_new > 0.95:
        mu_updated = 0.9 * mu_updated + 0.1 * 1.2
    return (p_nom_new, p_gl_new, p_flt_new), mu_updated


def _to_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MonitoringService:
    """Ingests timestamped temperature readings, updates trend filters, and manages alerts."""

    def __init__(
        self,
        store: MonitoringStore,
        policy: Policy | str = Policy.BAYESIAN,
        config: PolicyConfig | None = None,
        *,
        max_future_skew_s: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _require_finite(max_future_skew_s)
        if max_future_skew_s < 0.0:
            raise ValueError("max_future_skew_s cannot be negative")
        self.store = store
        self.policy = Policy(policy)
        self.cfg = config or PolicyConfig()
        self.max_future_skew_s = max_future_skew_s
        self._clock: Callable[[], datetime] = clock if clock is not None else (lambda: datetime.now(UTC))

    def ingest_reading(
        self,
        bay_id: str,
        temperature_c: float,
        *,
        sensor_id: str | None = None,
        recorded_at: datetime | None = None,
    ) -> IngestionResult:
        """Process one sensor reading atomically and persist updated equipment and alert state."""
        _require_finite(temperature_c)
        now_utc = _to_utc(self._clock())

        with self.store.transaction():
            eq = self.store.get_equipment(bay_id)
            resolved_sensor = eq.sensor_id if sensor_id is None else sensor_id
            if resolved_sensor != eq.sensor_id:
                raise SensorMismatchError(
                    f"Sensor {resolved_sensor!r} is not bound to bay {bay_id!r} (expected {eq.sensor_id!r})"
                )

            if recorded_at is not None:
                ts = _to_utc(recorded_at)
                if ts > now_utc + timedelta(seconds=self.max_future_skew_s):
                    raise InvalidTimestampError(
                        f"Reading timestamp {ts.isoformat()} exceeds allowed future clock skew "
                        f"({self.max_future_skew_s:g}s)"
                    )
            else:
                ts = now_utc
                if eq.last_reading_at is not None and ts <= eq.last_reading_at:
                    ts = eq.last_reading_at + timedelta(microseconds=1)

            delta_c = temperature_c - eq.baseline_temp_c
            existing = self.store.get_reading_by_timestamp(bay_id, ts)
            if existing is not None:
                if abs(existing.temperature_c - temperature_c) <= 1e-9:
                    return IngestionResult(
                        reading=existing,
                        equipment=eq,
                        alert=None,
                        alert_event=None,
                        applied_to_detector=False,
                        out_of_order=existing.out_of_order,
                        duplicate=True,
                    )
                raise ConflictingReadingError(f"Conflicting reading for bay {bay_id!r} at {ts.isoformat()}")

            if eq.last_reading_at is not None and ts < eq.last_reading_at:
                reading, unchanged_eq = self.store.record_late_reading(
                    bay_id=bay_id,
                    sensor_id=resolved_sensor,
                    temperature_c=temperature_c,
                    delta_c=delta_c,
                    recorded_at=ts,
                )
                return IngestionResult(
                    reading=reading,
                    equipment=unchanged_eq,
                    alert=None,
                    alert_event=None,
                    applied_to_detector=False,
                    out_of_order=True,
                    duplicate=False,
                )

            new_ewma = update_ewma(eq.ewma_delta_c, delta_c, self.cfg.ewma_alpha)
            new_cusum = update_cusum(eq.cusum_score, delta_c, self.cfg.cusum_drift_c)
            new_post, new_mu = update_bayesian_hmm(
                (eq.bayes_p_nom, eq.bayes_p_glitch, eq.bayes_p_fault),
                eq.bayes_mu_fault,
                delta_c,
            )

            status = eq.status
            alert_active = eq.alert_active
            alert_armed = eq.alert_armed
            rearm_after = eq.rearm_after
            open_alert: tuple[str, str, str, float] | None = None
            resolve_alerts = False
            alert_event: str | None = None

            if status != "confirmed":
                detector, metric, on_level, off_level, severity, msg = self._evaluate_detector(
                    delta_c=delta_c,
                    ewma=new_ewma,
                    cusum=new_cusum,
                    p_fault=new_post[2],
                )
                if not alert_armed and (metric < off_level or (rearm_after is not None and ts >= rearm_after)):
                    alert_armed = True
                    rearm_after = None

                if not alert_active and alert_armed and metric >= on_level:
                    alert_active = True
                    open_alert = (detector, severity, msg, round(metric, 4))
                    alert_event = "opened"
                elif alert_active and metric < off_level:
                    alert_active = False
                    resolve_alerts = True
                    alert_event = "resolved"
                status = "alert" if alert_active else "nominal"

            reading, updated_eq, alert_rec = self.store.record_reading_and_state(
                bay_id=bay_id,
                sensor_id=resolved_sensor,
                temperature_c=temperature_c,
                delta_c=delta_c,
                recorded_at=ts,
                status=status,
                ewma_delta_c=new_ewma,
                cusum_score=new_cusum,
                bayes_post=new_post,
                bayes_mu_fault=new_mu,
                alert_active=alert_active,
                alert_armed=alert_armed,
                rearm_after=rearm_after,
                open_alert=open_alert,
                resolve_alerts=resolve_alerts,
            )
            return IngestionResult(
                reading=reading,
                equipment=updated_eq,
                alert=alert_rec,
                alert_event=alert_event,
                applied_to_detector=True,
                out_of_order=False,
                duplicate=False,
            )

    def ingest_batch(self, readings: Sequence[ReadingInput]) -> list[IngestionResult]:
        """Validate and ingest a batch of readings inside a single atomic transaction."""
        with self.store.transaction():
            return [
                self.ingest_reading(
                    item.bay_id,
                    item.temperature_c,
                    sensor_id=item.sensor_id,
                    recorded_at=item.recorded_at,
                )
                for item in readings
            ]

    def _evaluate_detector(
        self,
        *,
        delta_c: float,
        ewma: float,
        cusum: float,
        p_fault: float,
    ) -> tuple[str, float, float, float, str, str]:
        if self.policy is Policy.BAYESIAN:
            severity = "high" if ewma >= 2.0 or delta_c >= 2.5 else "warning"
            return (
                "bayesian_hmm",
                p_fault,
                self.cfg.bayes_alert_prob,
                self.cfg.bayes_clear_prob,
                severity,
                f"Bayesian HMM P(fault)={p_fault:.2f} (delta {delta_c:+.1f} °C)",
            )
        if self.policy is Policy.CUSUM:
            severity = "high" if cusum >= 1.5 * self.cfg.cusum_threshold_c else "warning"
            return (
                "cusum",
                cusum,
                self.cfg.cusum_threshold_c,
                0.25 * self.cfg.cusum_threshold_c,
                severity,
                f"CUSUM S={cusum:.1f} °C·tick (delta {delta_c:+.1f} °C)",
            )
        severity = "high" if ewma >= 2.0 else "warning"
        return (
            "ewma",
            ewma,
            self.cfg.alert_on_c,
            self.cfg.alert_off_c,
            severity,
            f"EWMA telemetry {ewma:+.1f} °C above baseline",
        )
