"""Runtime settings and FastAPI dependency providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

from fastapi import Request

from ..inspection import InspectionPlanner
from ..monitoring import MonitoringService
from ..simulation import Policy
from ..storage import MonitoringStore

__all__ = ["AppSettings", "get_planner", "get_service", "get_store"]


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Configuration resolved from environment variables or explicit arguments."""

    db_path: str = "thermal_sentinel.db"
    policy: Policy = Policy.BAYESIAN

    @classmethod
    def from_env(cls) -> AppSettings:
        db_path = os.environ.get("THERMAL_SENTINEL_DB_PATH", "thermal_sentinel.db")
        policy_raw = os.environ.get("THERMAL_SENTINEL_POLICY", Policy.BAYESIAN.value)
        return cls(db_path=db_path, policy=Policy(policy_raw))


def get_store(request: Request) -> MonitoringStore:
    return cast(MonitoringStore, request.app.state.store)


def get_service(request: Request) -> MonitoringService:
    return cast(MonitoringService, request.app.state.service)


def get_planner(request: Request) -> InspectionPlanner:
    return cast(InspectionPlanner, request.app.state.planner)
