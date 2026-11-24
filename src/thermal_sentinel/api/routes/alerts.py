"""Alert listing and acknowledgement routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ...storage import MonitoringStore
from ..deps import get_store
from ..schemas import AlertResponse

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertResponse])
def list_alerts(
    store: Annotated[MonitoringStore, Depends(get_store)],
    status: Annotated[str | None, Query(pattern=r"^(open|acknowledged|confirmed|resolved)$")] = None,
    bay_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[AlertResponse]:
    rows = store.list_alerts(status=status, bay_id=bay_id, limit=limit)
    return [AlertResponse.model_validate(row) for row in rows]


@router.post("/{alert_id}/acknowledge", response_model=AlertResponse)
def acknowledge_alert(
    alert_id: Annotated[int, Path(ge=1)],
    store: Annotated[MonitoringStore, Depends(get_store)],
) -> AlertResponse:
    return AlertResponse.model_validate(store.acknowledge_alert(alert_id))
