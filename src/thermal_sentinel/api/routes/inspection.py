"""Robot inspection prioritization, close-range verification, and simulation replay routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from ...inspection import InspectionPlanner
from ...monitoring import MonitoringService
from ...simulation import Policy
from ...storage import MonitoringStore
from ..deps import get_planner, get_service, get_store
from ..schemas import (
    EquipmentResponse,
    InspectionDecisionResponse,
    InspectionLogResponse,
    InspectionVerifyRequest,
    ReplayResponse,
    SimulationReplayRequest,
    VerificationResponse,
)

router = APIRouter(prefix="/api/v1", tags=["inspection"])


@router.get("/inspection/next", response_model=InspectionDecisionResponse)
def next_inspection_target(
    planner: Annotated[InspectionPlanner, Depends(get_planner)],
    robot_x_m: Annotated[float, Query(ge=-1000.0, le=1000.0)] = 0.0,
    robot_y_m: Annotated[float, Query(ge=-1000.0, le=1000.0)] = 3.0,
    policy: Annotated[Policy | None, Query()] = None,
    log_dispatch: Annotated[bool, Query()] = False,
) -> InspectionDecisionResponse:
    decision = planner.next_target(
        robot_x_m=robot_x_m,
        robot_y_m=robot_y_m,
        policy=policy,
        log_dispatch=log_dispatch,
    )
    return InspectionDecisionResponse.model_validate(decision)


@router.post("/inspection/verify", response_model=VerificationResponse, status_code=status.HTTP_201_CREATED)
def verify_inspection(
    payload: InspectionVerifyRequest,
    planner: Annotated[InspectionPlanner, Depends(get_planner)],
) -> VerificationResponse:
    log, eq = planner.verify_inspection(
        bay_id=payload.bay_id,
        camera_temp_c=payload.camera_temp_c,
        robot_x_m=payload.robot_x_m,
        robot_y_m=payload.robot_y_m,
        inspected_at=payload.inspected_at,
    )
    return VerificationResponse(
        log=InspectionLogResponse.model_validate(log),
        equipment=EquipmentResponse.model_validate(eq),
    )


@router.get("/inspection/logs", response_model=list[InspectionLogResponse])
def list_inspection_logs(
    store: Annotated[MonitoringStore, Depends(get_store)],
    bay_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[InspectionLogResponse]:
    rows = store.list_inspection_logs(bay_id=bay_id, limit=limit)
    return [InspectionLogResponse.model_validate(row) for row in rows]


@router.post("/simulation/replay", response_model=ReplayResponse)
def replay_simulation(
    payload: SimulationReplayRequest,
    planner: Annotated[InspectionPlanner, Depends(get_planner)],
    service: Annotated[MonitoringService, Depends(get_service)],
) -> ReplayResponse:
    summary = planner.replay_scenario(
        policy=payload.policy or service.policy,
        seed=payload.seed,
        fault_bay_id=payload.fault_bay_id,
        max_ticks=payload.max_ticks,
    )
    return ReplayResponse.model_validate(summary)
