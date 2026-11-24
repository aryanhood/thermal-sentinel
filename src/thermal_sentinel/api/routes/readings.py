"""Sensor reading ingestion and history query routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from ...monitoring import IngestionResult, MonitoringService, ReadingInput
from ...storage import MonitoringStore
from ..deps import get_service, get_store
from ..schemas import (
    AlertResponse,
    BatchIngestionResponse,
    BatchReadingsRequest,
    EquipmentResponse,
    IngestionResponse,
    ReadingCreateRequest,
    SensorReadingResponse,
)

router = APIRouter(prefix="/api/v1/readings", tags=["readings"])


def _to_ingestion_response(result: IngestionResult) -> IngestionResponse:
    return IngestionResponse(
        reading=SensorReadingResponse.model_validate(result.reading),
        equipment=EquipmentResponse.model_validate(result.equipment),
        alert=None if result.alert is None else AlertResponse.model_validate(result.alert),
        alert_event=result.alert_event,
        applied_to_detector=result.applied_to_detector,
        out_of_order=result.out_of_order,
    )


@router.post("", response_model=IngestionResponse, status_code=status.HTTP_201_CREATED)
def ingest_reading(
    payload: ReadingCreateRequest,
    service: Annotated[MonitoringService, Depends(get_service)],
) -> IngestionResponse:
    result = service.ingest_reading(
        bay_id=payload.bay_id,
        temperature_c=payload.temperature_c,
        sensor_id=payload.sensor_id,
        recorded_at=payload.recorded_at,
    )
    return _to_ingestion_response(result)


@router.post("/batch", response_model=BatchIngestionResponse, status_code=status.HTTP_201_CREATED)
def ingest_batch(
    payload: BatchReadingsRequest,
    service: Annotated[MonitoringService, Depends(get_service)],
) -> BatchIngestionResponse:
    items = [
        ReadingInput(
            bay_id=item.bay_id,
            temperature_c=item.temperature_c,
            sensor_id=item.sensor_id,
            recorded_at=item.recorded_at,
        )
        for item in payload.readings
    ]
    results = service.ingest_batch(items)
    opened = sum(1 for item in results if item.alert_event == "opened")
    resolved = sum(1 for item in results if item.alert_event == "resolved")
    out_of_order = sum(1 for item in results if item.out_of_order)
    return BatchIngestionResponse(
        ingested_count=len(results),
        alerts_opened=opened,
        alerts_resolved=resolved,
        out_of_order_count=out_of_order,
        results=[_to_ingestion_response(item) for item in results],
    )


@router.get("", response_model=list[SensorReadingResponse])
def list_readings(
    store: Annotated[MonitoringStore, Depends(get_store)],
    bay_id: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[SensorReadingResponse]:
    rows = store.list_readings(bay_id=bay_id, since=since, limit=limit)
    return [SensorReadingResponse.model_validate(row) for row in rows]
