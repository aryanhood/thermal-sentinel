"""Equipment inventory and baseline configuration routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from ...storage import MonitoringStore
from ..deps import get_store
from ..schemas import BaselineUpdateRequest, EquipmentCreateRequest, EquipmentResponse

router = APIRouter(prefix="/api/v1/equipment", tags=["equipment"])


@router.get("", response_model=list[EquipmentResponse])
def list_equipment(store: Annotated[MonitoringStore, Depends(get_store)]) -> list[EquipmentResponse]:
    return [EquipmentResponse.model_validate(item) for item in store.list_equipment()]


@router.get("/{bay_id}", response_model=EquipmentResponse)
def get_equipment(
    bay_id: str,
    store: Annotated[MonitoringStore, Depends(get_store)],
) -> EquipmentResponse:
    return EquipmentResponse.model_validate(store.get_equipment(bay_id))


@router.post("", response_model=EquipmentResponse, status_code=status.HTTP_201_CREATED)
def create_equipment(
    payload: EquipmentCreateRequest,
    store: Annotated[MonitoringStore, Depends(get_store)],
) -> EquipmentResponse:
    record = store.create_equipment(
        bay_id=payload.bay_id,
        label=payload.label,
        x_m=payload.x_m,
        y_m=payload.y_m,
        baseline_temp_c=payload.baseline_temp_c,
        sensor_id=payload.sensor_id,
    )
    return EquipmentResponse.model_validate(record)


@router.patch("/{bay_id}/baseline", response_model=EquipmentResponse)
def update_baseline(
    bay_id: str,
    payload: BaselineUpdateRequest,
    store: Annotated[MonitoringStore, Depends(get_store)],
) -> EquipmentResponse:
    record = store.update_baseline(bay_id, payload.baseline_temp_c)
    return EquipmentResponse.model_validate(record)
