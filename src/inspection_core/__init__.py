"""Deterministic decision core for the Thermal Sentinel demo."""

from .engine import (
    ComparisonResult,
    Experiment,
    MissionResult,
    Scenario,
    SimulationConfig,
    default_scenario,
    run_comparison,
    run_pair,
    run_policy,
)

__all__ = [
    "ComparisonResult",
    "Experiment",
    "MissionResult",
    "Scenario",
    "SimulationConfig",
    "default_scenario",
    "run_comparison",
    "run_pair",
    "run_policy",
]
