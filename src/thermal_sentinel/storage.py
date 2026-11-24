"""SQLite persistence layer for equipment state, sensor readings, alerts, and inspection logs."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from .model import DEFAULT_BAYS

__all__ = [
    "AlertNotFoundError",
    "AlertRecord",
    "EquipmentExistsError",
    "EquipmentNotFoundError",
    "EquipmentRecord",
    "InspectionLogRecord",
    "MonitoringStore",
    "SensorReadingRecord",
]

_DEFAULT_BAYES_NOM = 0.995
_DEFAULT_BAYES_GLITCH = 0.004
_DEFAULT_BAYES_FAULT = 0.001
_DEFAULT_BAYES_MU = 1.2

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS equipment (
    bay_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    x_m REAL NOT NULL,
    y_m REAL NOT NULL,
    baseline_temp_c REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'nominal',
    latest_temp_c REAL,
    latest_delta_c REAL,
    ewma_delta_c REAL NOT NULL DEFAULT 0.0,
    cusum_score REAL NOT NULL DEFAULT 0.0,
    bayes_p_nom REAL NOT NULL DEFAULT 0.995,
    bayes_p_glitch REAL NOT NULL DEFAULT 0.004,
    bayes_p_fault REAL NOT NULL DEFAULT 0.001,
    bayes_mu_fault REAL NOT NULL DEFAULT 1.2,
    alert_active INTEGER NOT NULL DEFAULT 0,
    alert_armed INTEGER NOT NULL DEFAULT 1,
    rearm_after TEXT,
    last_reading_at TEXT,
    last_inspected_at TEXT
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bay_id TEXT NOT NULL REFERENCES equipment(bay_id) ON DELETE CASCADE,
    sensor_id TEXT NOT NULL,
    temperature_c REAL NOT NULL,
    delta_c REAL NOT NULL,
    recorded_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_readings_bay_time
ON sensor_readings (bay_id, recorded_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bay_id TEXT NOT NULL REFERENCES equipment(bay_id) ON DELETE CASCADE,
    detector TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    metric_value REAL NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_status_bay
ON alerts (status, bay_id, id DESC);

CREATE TABLE IF NOT EXISTS inspection_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bay_id TEXT NOT NULL REFERENCES equipment(bay_id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    policy TEXT NOT NULL,
    priority_score REAL NOT NULL,
    reason TEXT NOT NULL,
    robot_x_m REAL NOT NULL,
    robot_y_m REAL NOT NULL,
    camera_temp_c REAL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_bay_id
ON inspection_logs (bay_id, id DESC);
"""


class EquipmentNotFoundError(KeyError):
    """Raised when referencing a bay that does not exist in storage."""

    def __init__(self, bay_id: str) -> None:
        self.bay_id = bay_id
        super().__init__(f"Unknown equipment bay: {bay_id}")

    def __str__(self) -> str:
        return str(self.args[0])


class EquipmentExistsError(ValueError):
    """Raised when attempting to register a duplicate equipment bay."""


class AlertNotFoundError(KeyError):
    """Raised when referencing an alert id that does not exist in storage."""

    def __init__(self, alert_id: int) -> None:
        self.alert_id = alert_id
        super().__init__(f"Unknown alert id: {alert_id}")

    def __str__(self) -> str:
        return str(self.args[0])


@dataclass(frozen=True, slots=True)
class EquipmentRecord:
    bay_id: str
    label: str
    x_m: float
    y_m: float
    baseline_temp_c: float
    status: str
    latest_temp_c: float | None
    latest_delta_c: float | None
    ewma_delta_c: float
    cusum_score: float
    bayes_p_nom: float
    bayes_p_glitch: float
    bayes_p_fault: float
    bayes_mu_fault: float
    alert_active: bool
    alert_armed: bool
    rearm_after: datetime | None
    last_reading_at: datetime | None
    last_inspected_at: datetime | None


@dataclass(frozen=True, slots=True)
class SensorReadingRecord:
    id: int
    bay_id: str
    sensor_id: str
    temperature_c: float
    delta_c: float
    recorded_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, slots=True)
class AlertRecord:
    id: int
    bay_id: str
    detector: str
    severity: str
    status: str
    message: str
    metric_value: float
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class InspectionLogRecord:
    id: int
    bay_id: str
    mode: str
    policy: str
    priority_score: float
    reason: str
    robot_x_m: float
    robot_y_m: float
    camera_temp_c: float | None
    outcome: str
    created_at: datetime


def _to_utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _fmt_ts(value: datetime) -> str:
    return _to_utc(value).isoformat(timespec="microseconds")


def _parse_ts(value: str) -> datetime:
    return _to_utc(datetime.fromisoformat(value))


def _parse_optional_ts(value: str | None) -> datetime | None:
    return None if value is None else _parse_ts(value)


def _row_to_equipment(row: sqlite3.Row) -> EquipmentRecord:
    return EquipmentRecord(
        bay_id=str(row["bay_id"]),
        label=str(row["label"]),
        x_m=float(row["x_m"]),
        y_m=float(row["y_m"]),
        baseline_temp_c=float(row["baseline_temp_c"]),
        status=str(row["status"]),
        latest_temp_c=None if row["latest_temp_c"] is None else float(row["latest_temp_c"]),
        latest_delta_c=None if row["latest_delta_c"] is None else float(row["latest_delta_c"]),
        ewma_delta_c=float(row["ewma_delta_c"]),
        cusum_score=float(row["cusum_score"]),
        bayes_p_nom=float(row["bayes_p_nom"]),
        bayes_p_glitch=float(row["bayes_p_glitch"]),
        bayes_p_fault=float(row["bayes_p_fault"]),
        bayes_mu_fault=float(row["bayes_mu_fault"]),
        alert_active=bool(row["alert_active"]),
        alert_armed=bool(row["alert_armed"]),
        rearm_after=_parse_optional_ts(row["rearm_after"]),
        last_reading_at=_parse_optional_ts(row["last_reading_at"]),
        last_inspected_at=_parse_optional_ts(row["last_inspected_at"]),
    )


def _row_to_reading(row: sqlite3.Row) -> SensorReadingRecord:
    return SensorReadingRecord(
        id=int(row["id"]),
        bay_id=str(row["bay_id"]),
        sensor_id=str(row["sensor_id"]),
        temperature_c=float(row["temperature_c"]),
        delta_c=float(row["delta_c"]),
        recorded_at=_parse_ts(str(row["recorded_at"])),
        ingested_at=_parse_ts(str(row["ingested_at"])),
    )


def _row_to_alert(row: sqlite3.Row) -> AlertRecord:
    return AlertRecord(
        id=int(row["id"]),
        bay_id=str(row["bay_id"]),
        detector=str(row["detector"]),
        severity=str(row["severity"]),
        status=str(row["status"]),
        message=str(row["message"]),
        metric_value=float(row["metric_value"]),
        created_at=_parse_ts(str(row["created_at"])),
        resolved_at=_parse_optional_ts(row["resolved_at"]),
    )


def _row_to_log(row: sqlite3.Row) -> InspectionLogRecord:
    return InspectionLogRecord(
        id=int(row["id"]),
        bay_id=str(row["bay_id"]),
        mode=str(row["mode"]),
        policy=str(row["policy"]),
        priority_score=float(row["priority_score"]),
        reason=str(row["reason"]),
        robot_x_m=float(row["robot_x_m"]),
        robot_y_m=float(row["robot_y_m"]),
        camera_temp_c=None if row["camera_temp_c"] is None else float(row["camera_temp_c"]),
        outcome=str(row["outcome"]),
        created_at=_parse_ts(str(row["created_at"])),
    )


class MonitoringStore:
    """Thread-safe SQLite store for thermal monitoring state and audit logs."""

    def __init__(
        self,
        db_path: str | Path = "thermal_sentinel.db",
        *,
        seed_defaults: bool = True,
        default_baseline_c: float = 28.0,
    ) -> None:
        target = str(db_path)
        self.db_path = target
        if target != ":memory:" and not target.startswith("file:"):
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON;")
            if target != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL;")
                self._conn.execute("PRAGMA busy_timeout = 5000;")
            self._conn.executescript(_SCHEMA_SQL)
            if seed_defaults:
                count_row = self._conn.execute("SELECT COUNT(*) AS n FROM equipment").fetchone()
                if count_row is not None and int(count_row["n"]) == 0:
                    self._conn.executemany(
                        """
                        INSERT INTO equipment (bay_id, label, x_m, y_m, baseline_temp_c)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        [(bay.id, bay.label, bay.x, bay.y, default_baseline_c) for bay in DEFAULT_BAYS],
                    )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ping(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 AS ok").fetchone()
            return row is not None and int(row["ok"]) == 1

    # -- equipment -----------------------------------------------------------------------
    def list_equipment(self) -> list[EquipmentRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM equipment ORDER BY bay_id ASC").fetchall()
            return [_row_to_equipment(row) for row in rows]

    def get_equipment(self, bay_id: str) -> EquipmentRecord:
        with self._lock:
            row = self._conn.execute("SELECT * FROM equipment WHERE bay_id = ?", (bay_id,)).fetchone()
            if row is None:
                raise EquipmentNotFoundError(bay_id)
            return _row_to_equipment(row)

    def create_equipment(
        self,
        bay_id: str,
        label: str,
        x_m: float,
        y_m: float,
        baseline_temp_c: float = 28.0,
    ) -> EquipmentRecord:
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO equipment (bay_id, label, x_m, y_m, baseline_temp_c)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (bay_id, label, x_m, y_m, baseline_temp_c),
                )
            except sqlite3.IntegrityError as exc:
                raise EquipmentExistsError(f"Equipment bay {bay_id!r} already exists") from exc
            return self.get_equipment(bay_id)

    def update_baseline(self, bay_id: str, baseline_temp_c: float) -> EquipmentRecord:
        with self._lock, self._conn:
            self.get_equipment(bay_id)
            self._conn.execute(
                """
                UPDATE equipment
                SET baseline_temp_c = ?,
                    status = 'nominal',
                    latest_temp_c = NULL,
                    latest_delta_c = NULL,
                    ewma_delta_c = 0.0,
                    cusum_score = 0.0,
                    bayes_p_nom = ?,
                    bayes_p_glitch = ?,
                    bayes_p_fault = ?,
                    bayes_mu_fault = ?,
                    alert_active = 0,
                    alert_armed = 1,
                    rearm_after = NULL
                WHERE bay_id = ?
                """,
                (
                    baseline_temp_c,
                    _DEFAULT_BAYES_NOM,
                    _DEFAULT_BAYES_GLITCH,
                    _DEFAULT_BAYES_FAULT,
                    _DEFAULT_BAYES_MU,
                    bay_id,
                ),
            )
            return self.get_equipment(bay_id)

    # -- readings & alerts ---------------------------------------------------------------
    def record_reading_and_state(
        self,
        *,
        bay_id: str,
        sensor_id: str,
        temperature_c: float,
        delta_c: float,
        recorded_at: datetime,
        status: str,
        ewma_delta_c: float,
        cusum_score: float,
        bayes_post: tuple[float, float, float],
        bayes_mu_fault: float,
        alert_active: bool,
        alert_armed: bool,
        rearm_after: datetime | None,
        open_alert: tuple[str, str, str, float] | None = None,
        resolve_alerts: bool = False,
    ) -> tuple[SensorReadingRecord, EquipmentRecord, AlertRecord | None]:
        recorded_ts = _fmt_ts(recorded_at)
        ingested_ts = _fmt_ts(_to_utc())
        rearm_ts = None if rearm_after is None else _fmt_ts(rearm_after)

        with self._lock, self._conn:
            self.get_equipment(bay_id)
            cur = self._conn.execute(
                """
                INSERT INTO sensor_readings (bay_id, sensor_id, temperature_c, delta_c, recorded_at, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bay_id, sensor_id, temperature_c, delta_c, recorded_ts, ingested_ts),
            )
            reading_id = int(cur.lastrowid or 0)
            self._conn.execute(
                """
                UPDATE equipment
                SET status = ?,
                    latest_temp_c = ?,
                    latest_delta_c = ?,
                    ewma_delta_c = ?,
                    cusum_score = ?,
                    bayes_p_nom = ?,
                    bayes_p_glitch = ?,
                    bayes_p_fault = ?,
                    bayes_mu_fault = ?,
                    alert_active = ?,
                    alert_armed = ?,
                    rearm_after = ?,
                    last_reading_at = ?
                WHERE bay_id = ?
                """,
                (
                    status,
                    temperature_c,
                    delta_c,
                    ewma_delta_c,
                    cusum_score,
                    bayes_post[0],
                    bayes_post[1],
                    bayes_post[2],
                    bayes_mu_fault,
                    int(alert_active),
                    int(alert_armed),
                    rearm_ts,
                    recorded_ts,
                    bay_id,
                ),
            )

            alert_record: AlertRecord | None = None
            if open_alert is not None:
                detector, severity, message, metric_value = open_alert
                alert_cur = self._conn.execute(
                    """
                    INSERT INTO alerts (bay_id, detector, severity, status, message, metric_value, created_at)
                    VALUES (?, ?, ?, 'open', ?, ?, ?)
                    """,
                    (bay_id, detector, severity, message, metric_value, recorded_ts),
                )
                alert_id = int(alert_cur.lastrowid or 0)
                alert_row = self._conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
                if alert_row is not None:  # pragma: no branch
                    alert_record = _row_to_alert(alert_row)
            elif resolve_alerts:
                open_rows = self._conn.execute(
                    """
                    SELECT id FROM alerts
                    WHERE bay_id = ? AND status IN ('open', 'acknowledged')
                    ORDER BY id DESC
                    """,
                    (bay_id,),
                ).fetchall()
                if open_rows:
                    latest_id = int(open_rows[0]["id"])
                    self._conn.execute(
                        """
                        UPDATE alerts
                        SET status = 'resolved', resolved_at = ?
                        WHERE bay_id = ? AND status IN ('open', 'acknowledged')
                        """,
                        (recorded_ts, bay_id),
                    )
                    resolved_row = self._conn.execute("SELECT * FROM alerts WHERE id = ?", (latest_id,)).fetchone()
                    if resolved_row is not None:  # pragma: no branch
                        alert_record = _row_to_alert(resolved_row)

            reading_row = self._conn.execute("SELECT * FROM sensor_readings WHERE id = ?", (reading_id,)).fetchone()
            assert reading_row is not None
            return _row_to_reading(reading_row), self.get_equipment(bay_id), alert_record

    def list_readings(
        self,
        *,
        bay_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[SensorReadingRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if bay_id is not None:
            clauses.append("bay_id = ?")
            params.append(bay_id)
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(_fmt_ts(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM sensor_readings {where} ORDER BY recorded_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
            return [_row_to_reading(row) for row in rows]

    def list_alerts(
        self,
        *,
        status: str | None = None,
        bay_id: str | None = None,
        limit: int = 100,
    ) -> list[AlertRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if bay_id is not None:
            clauses.append("bay_id = ?")
            params.append(bay_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM alerts {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
            return [_row_to_alert(row) for row in rows]

    def acknowledge_alert(self, alert_id: int) -> AlertRecord:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
            if row is None:
                raise AlertNotFoundError(alert_id)
            if str(row["status"]) == "open":
                self._conn.execute("UPDATE alerts SET status = 'acknowledged' WHERE id = ?", (alert_id,))
                updated = self._conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
                assert updated is not None
                return _row_to_alert(updated)
            return _row_to_alert(row)

    # -- inspection logs & verification --------------------------------------------------
    def record_inspection_log(
        self,
        *,
        bay_id: str,
        mode: str,
        policy: str,
        priority_score: float,
        reason: str,
        robot_x_m: float,
        robot_y_m: float,
        camera_temp_c: float | None,
        outcome: str,
        created_at: datetime | None = None,
    ) -> InspectionLogRecord:
        ts = _fmt_ts(_to_utc(created_at))
        with self._lock, self._conn:
            self.get_equipment(bay_id)
            cur = self._conn.execute(
                """
                INSERT INTO inspection_logs (
                    bay_id, mode, policy, priority_score, reason,
                    robot_x_m, robot_y_m, camera_temp_c, outcome, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (bay_id, mode, policy, priority_score, reason, robot_x_m, robot_y_m, camera_temp_c, outcome, ts),
            )
            log_id = int(cur.lastrowid or 0)
            row = self._conn.execute("SELECT * FROM inspection_logs WHERE id = ?", (log_id,)).fetchone()
            assert row is not None
            return _row_to_log(row)

    def record_verification(
        self,
        *,
        bay_id: str,
        policy: str,
        reason: str,
        robot_x_m: float,
        robot_y_m: float,
        camera_temp_c: float,
        outcome: str,
        inspected_at: datetime,
        status: str,
        cusum_score: float,
        bayes_post: tuple[float, float, float],
        bayes_mu_fault: float,
        alert_active: bool,
        alert_armed: bool,
        rearm_after: datetime | None,
        alert_resolution_status: str | None = None,
    ) -> tuple[InspectionLogRecord, EquipmentRecord]:
        ts = _fmt_ts(inspected_at)
        rearm_ts = None if rearm_after is None else _fmt_ts(rearm_after)
        with self._lock, self._conn:
            self.get_equipment(bay_id)
            self._conn.execute(
                """
                UPDATE equipment
                SET status = ?,
                    cusum_score = ?,
                    bayes_p_nom = ?,
                    bayes_p_glitch = ?,
                    bayes_p_fault = ?,
                    bayes_mu_fault = ?,
                    alert_active = ?,
                    alert_armed = ?,
                    rearm_after = ?,
                    last_inspected_at = ?
                WHERE bay_id = ?
                """,
                (
                    status,
                    cusum_score,
                    bayes_post[0],
                    bayes_post[1],
                    bayes_post[2],
                    bayes_mu_fault,
                    int(alert_active),
                    int(alert_armed),
                    rearm_ts,
                    ts,
                    bay_id,
                ),
            )
            if alert_resolution_status is not None:
                self._conn.execute(
                    """
                    UPDATE alerts
                    SET status = ?, resolved_at = ?
                    WHERE bay_id = ? AND status IN ('open', 'acknowledged')
                    """,
                    (alert_resolution_status, ts, bay_id),
                )
            log = self.record_inspection_log(
                bay_id=bay_id,
                mode="verification",
                policy=policy,
                priority_score=1.0,
                reason=reason,
                robot_x_m=robot_x_m,
                robot_y_m=robot_y_m,
                camera_temp_c=camera_temp_c,
                outcome=outcome,
                created_at=inspected_at,
            )
            return log, self.get_equipment(bay_id)

    def list_inspection_logs(self, *, bay_id: str | None = None, limit: int = 100) -> list[InspectionLogRecord]:
        where = "WHERE bay_id = ?" if bay_id is not None else ""
        params: list[object] = [bay_id, limit] if bay_id is not None else [limit]
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM inspection_logs {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
            return [_row_to_log(row) for row in rows]

    def reset_monitoring_state(self, *, baseline_overrides: dict[str, float] | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sensor_readings")
            self._conn.execute("DELETE FROM alerts")
            self._conn.execute("DELETE FROM inspection_logs")
            self._conn.execute(
                """
                UPDATE equipment
                SET status = 'nominal',
                    latest_temp_c = NULL,
                    latest_delta_c = NULL,
                    ewma_delta_c = 0.0,
                    cusum_score = 0.0,
                    bayes_p_nom = ?,
                    bayes_p_glitch = ?,
                    bayes_p_fault = ?,
                    bayes_mu_fault = ?,
                    alert_active = 0,
                    alert_armed = 1,
                    rearm_after = NULL,
                    last_reading_at = NULL,
                    last_inspected_at = NULL
                """,
                (_DEFAULT_BAYES_NOM, _DEFAULT_BAYES_GLITCH, _DEFAULT_BAYES_FAULT, _DEFAULT_BAYES_MU),
            )
            if baseline_overrides:
                for bay_id, baseline_c in baseline_overrides.items():
                    self._conn.execute(
                        "UPDATE equipment SET baseline_temp_c = ? WHERE bay_id = ?",
                        (baseline_c, bay_id),
                    )
