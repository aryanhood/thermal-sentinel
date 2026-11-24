"""Health check route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from ... import __version__
from ...monitoring import MonitoringService
from ...storage import MonitoringStore
from ..deps import get_service, get_store
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(
    response: Response,
    store: Annotated[MonitoringStore, Depends(get_store)],
    service: Annotated[MonitoringService, Depends(get_service)],
) -> HealthResponse:
    db_ok = store.ping()
    if db_ok:
        equipment_count, open_alerts = store.count_summary()
        overall = "ok"
    else:
        equipment_count, open_alerts = 0, 0
        overall = "degraded"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status=overall,
        version=__version__,
        database="ok" if db_ok else "error",
        policy=service.policy,
        equipment_count=equipment_count,
        open_alerts=open_alerts,
    )
