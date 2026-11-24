"""FastAPI application factory for the Thermal Sentinel monitoring prototype."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from .. import __version__
from ..inspection import InspectionPlanner
from ..monitoring import MonitoringService
from ..simulation import Policy, PolicyConfig
from ..storage import AlertNotFoundError, EquipmentExistsError, EquipmentNotFoundError, MonitoringStore
from .deps import AppSettings
from .routes import alerts, equipment, health, inspection, readings

__all__ = ["app", "create_app"]


def create_app(
    db_path: str | Path | None = None,
    policy: Policy | str | None = None,
    config: PolicyConfig | None = None,
) -> FastAPI:
    """Create a configured FastAPI application instance."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        env_settings = AppSettings.from_env()
        resolved_db = env_settings.db_path if db_path is None else str(db_path)
        resolved_policy = env_settings.policy if policy is None else Policy(policy)
        store = MonitoringStore(resolved_db)
        application.state.store = store
        application.state.service = MonitoringService(store, policy=resolved_policy, config=config)
        application.state.planner = InspectionPlanner(store, policy=resolved_policy, config=config)
        try:
            yield
        finally:
            store.close()

    application = FastAPI(
        title="Thermal Sentinel API",
        version=__version__,
        description=(
            "Industrial thermal monitoring prototype combining REST sensor ingestion, "
            "SQLite storage, sequential anomaly detection, and robot inspection planning."
        ),
        lifespan=lifespan,
    )

    @application.exception_handler(EquipmentNotFoundError)
    @application.exception_handler(AlertNotFoundError)
    def handle_not_found(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})

    @application.exception_handler(EquipmentExistsError)
    def handle_conflict(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    @application.exception_handler(ValueError)
    def handle_value_error(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})

    application.include_router(health.router)
    application.include_router(equipment.router)
    application.include_router(readings.router)
    application.include_router(alerts.router)
    application.include_router(inspection.router)
    return application


app = create_app()
