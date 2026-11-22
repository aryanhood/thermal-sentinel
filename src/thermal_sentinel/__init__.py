"""Thermal Sentinel: paired evaluation of fixed vs. risk-adaptive inspection patrols."""

__version__ = "0.1.0"

from .environment import Environment
from .model import DEFAULT_BAYS, Bay, Scenario, make_scenario
from .simulation import PairResult, Policy, PolicyConfig, RunResult, run_pair, run_policy

__all__ = [
    "DEFAULT_BAYS",
    "Bay",
    "Environment",
    "PairResult",
    "Policy",
    "PolicyConfig",
    "RunResult",
    "Scenario",
    "__version__",
    "make_scenario",
    "run_pair",
    "run_policy",
]
