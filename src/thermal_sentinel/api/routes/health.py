"""Health check route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ... import __version__
from ...monitoring import MonitoringService
from ...storage import MonitoringStore
from ..deps import get_service, get_store
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(
    store: Annotated[MonitoringStore, Depends(get_store)],
    service: Annotated[MonitoringService, Depends(get_service)],
) -> HealthResponse:
    db_ok = store.ping()
    equipment_count = len(store.list_equipment())
    open_alerts = len(store.list_alerts(status="open", limit=1000))
    return HealthResponse(
        status="ok",
        version=__version__,
        database="ok" if db_ok else "error",
        policy=service.policy,
        equipment_count=equipment_count,
        open_alerts=open_alerts,
    )
