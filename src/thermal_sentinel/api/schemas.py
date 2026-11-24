"""Pydantic v2 request and response schemas for the monitoring REST API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..simulation import Policy

__all__ = [
    "AlertResponse",
    "BaselineUpdateRequest",
    "BatchIngestionResponse",
    "BatchReadingsRequest",
    "EquipmentCreateRequest",
    "EquipmentResponse",
    "HealthResponse",
    "IngestionResponse",
    "InspectionDecisionResponse",
    "InspectionLogResponse",
    "InspectionVerifyRequest",
    "ReadingCreateRequest",
    "ReplayResponse",
    "SensorReadingResponse",
    "SimulationReplayRequest",
    "VerificationResponse",
]


class ReadingCreateRequest(BaseModel):
    bay_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    temperature_c: float = Field(ge=-50.0, le=500.0, allow_inf_nan=False)
    sensor_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    recorded_at: datetime | None = None


class BatchReadingsRequest(BaseModel):
    readings: list[ReadingCreateRequest] = Field(min_length=1, max_length=1000)


class EquipmentCreateRequest(BaseModel):
    bay_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    label: str = Field(min_length=1, max_length=64)
    sensor_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    x_m: float = Field(ge=-1000.0, le=1000.0, allow_inf_nan=False)
    y_m: float = Field(ge=-1000.0, le=1000.0, allow_inf_nan=False)
    baseline_temp_c: float = Field(default=28.0, ge=-50.0, le=500.0, allow_inf_nan=False)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("label must not be blank")
        return cleaned


class BaselineUpdateRequest(BaseModel):
    baseline_temp_c: float = Field(ge=-50.0, le=500.0, allow_inf_nan=False)


class InspectionVerifyRequest(BaseModel):
    bay_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    camera_temp_c: float = Field(ge=-50.0, le=500.0, allow_inf_nan=False)
    robot_x_m: float = Field(default=0.0, ge=-1000.0, le=1000.0, allow_inf_nan=False)
    robot_y_m: float = Field(default=3.0, ge=-1000.0, le=1000.0, allow_inf_nan=False)
    inspected_at: datetime | None = None


class SimulationReplayRequest(BaseModel):
    """Parameters for an isolated in-memory simulation replay (does not alter stored monitoring data)."""

    seed: int = Field(default=3, ge=0, le=2**31 - 1)
    fault_bay_id: str | None = Field(default="B5", pattern=r"^[A-Za-z0-9_-]{1,32}$")
    max_ticks: int = Field(default=120, ge=1, le=360)
    policy: Policy | None = None


class EquipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bay_id: str
    label: str
    sensor_id: str
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


class SensorReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bay_id: str
    sensor_id: str
    temperature_c: float
    delta_c: float
    recorded_at: datetime
    ingested_at: datetime
    out_of_order: bool


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bay_id: str
    detector: str
    severity: str
    status: str
    message: str
    metric_value: float
    created_at: datetime
    resolved_at: datetime | None


class IngestionResponse(BaseModel):
    reading: SensorReadingResponse
    equipment: EquipmentResponse
    alert: AlertResponse | None
    alert_event: str | None
    applied_to_detector: bool
    out_of_order: bool
    duplicate: bool


class BatchIngestionResponse(BaseModel):
    total_count: int
    created_count: int
    duplicate_count: int
    out_of_order_count: int
    alerts_opened: int
    alerts_resolved: int
    results: list[IngestionResponse]


class InspectionDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class InspectionLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class VerificationResponse(BaseModel):
    log: InspectionLogResponse
    equipment: EquipmentResponse


class ReplayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    policy: Policy
    equipment_count: int
    open_alerts: int
