"""Thermal Sentinel: paired evaluation of fixed vs. risk-adaptive inspection patrols."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("thermal-sentinel")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0+local"

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
