"""Deterministic evidence suite for the Thermal Sentinel decision policy.

The benchmark deliberately keeps the simulator configuration fixed while
crossing four random seeds with all six possible fault bays.  Both policies
receive the exact same scenario object, so the reported difference is due to
the patrol policy rather than different simulated conditions.
"""

from __future__ import annotations

import argparse
import json
import math
from statistics import fmean, median
import sys
from typing import Any, Iterable, Sequence

from inspection_core import default_scenario, run_comparison


DEFAULT_SEEDS: tuple[int, ...] = (11, 23, 37, 53)
DEFAULT_FAULT_BAYS: tuple[str, ...] = ("B1", "B2", "B3", "B4", "B5", "B6")
SCHEMA_VERSION = "1.0"


def _round(value: float) -> float:
    return round(value, 2)


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    """Return a linearly interpolated percentile without external packages."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return _round(ordered[lower])
    weight = position - lower
    return _round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _stats(values: Iterable[float]) -> dict[str, int | float | None]:
    samples = list(values)
    if not samples:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "mean": None,
            "p95": None,
            "max": None,
        }
    return {
        "count": len(samples),
        "min": _round(min(samples)),
        "median": _round(median(samples)),
        "mean": _round(fmean(samples)),
        "p95": _percentile(samples, 0.95),
        "max": _round(max(samples)),
    }


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(100.0 * numerator / denominator, 1)


def _outcome(fixed_time: float | None, adaptive_time: float | None) -> str:
    if fixed_time is None and adaptive_time is None:
        return "neither_confirmed"
    if fixed_time is None:
        return "adaptive_only_confirmation"
    if adaptive_time is None:
        return "fixed_only_confirmation"
    if adaptive_time < fixed_time:
        return "adaptive_faster"
    if fixed_time < adaptive_time:
        return "fixed_faster"
    return "tie"


def _selected_kpis(kpis: dict[str, Any]) -> dict[str, Any]:
    return {
        "time_to_confirm_s": kpis["time_to_confirm_s"],
        "mission_elapsed_s": kpis["mission_elapsed_s"],
        "distance_m": kpis["distance_m"],
        "coverage_pct": kpis["coverage_pct"],
        "max_unseen_s": kpis["max_unseen_s"],
        "confirmed_hotspots": kpis["confirmed_hotspots"],
        "guardrails_respected": kpis["guardrails_respected"],
    }


def run_benchmark(
    seeds: Sequence[int] = DEFAULT_SEEDS,
    fault_bays: Sequence[str] = DEFAULT_FAULT_BAYS,
) -> dict[str, Any]:
    """Run the scenario matrix and return a JSON-serializable evidence report."""
    seeds = tuple(seeds)
    fault_bays = tuple(fault_bays)
    if not seeds or not fault_bays:
        raise ValueError("at least one seed and one fault bay are required")

    cases: list[dict[str, Any]] = []
    for seed in seeds:
        for fault_bay_id in fault_bays:
            scenario = default_scenario(seed, fault_bay_id=fault_bay_id)
            comparison = run_comparison(scenario)
            fixed = comparison.fixed.kpis
            adaptive = comparison.adaptive.kpis
            fixed_time = fixed["time_to_confirm_s"]
            adaptive_time = adaptive["time_to_confirm_s"]
            paired_time_saved = (
                None
                if fixed_time is None or adaptive_time is None
                else _round(fixed_time - adaptive_time)
            )
            fixed_distance = float(fixed["distance_m"])
            adaptive_distance = float(adaptive["distance_m"])
            cases.append(
                {
                    "case_id": f"seed-{seed}-{fault_bay_id}",
                    "seed": seed,
                    "fault_bay_id": fault_bay_id,
                    "outcome": _outcome(fixed_time, adaptive_time),
                    "time_saved_s": paired_time_saved,
                    "distance_delta_m": _round(adaptive_distance - fixed_distance),
                    "fixed": _selected_kpis(fixed),
                    "adaptive": _selected_kpis(adaptive),
                }
            )

    total = len(cases)
    outcome_counts = {
        name: sum(case["outcome"] == name for case in cases)
        for name in (
            "adaptive_faster",
            "adaptive_only_confirmation",
            "fixed_faster",
            "fixed_only_confirmation",
            "tie",
            "neither_confirmed",
        )
    }
    adaptive_wins = (
        outcome_counts["adaptive_faster"]
        + outcome_counts["adaptive_only_confirmation"]
    )
    fixed_wins = outcome_counts["fixed_faster"] + outcome_counts["fixed_only_confirmation"]
    evaluated = total - outcome_counts["neither_confirmed"]
    both_full_coverage = sum(
        case["fixed"]["coverage_pct"] == 100.0
        and case["adaptive"]["coverage_pct"] == 100.0
        for case in cases
    )
    fixed_confirmations = sum(case["fixed"]["confirmed_hotspots"] > 0 for case in cases)
    adaptive_confirmations = sum(case["adaptive"]["confirmed_hotspots"] > 0 for case in cases)

    reference = default_scenario(seeds[0], fault_bay_id=fault_bays[0])
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark": {
            "name": "thermal-sentinel-policy-comparison",
            "scenario_count": total,
            "seeds": list(seeds),
            "fault_bays": list(fault_bays),
            "policies": ["fixed", "adaptive"],
            "controlled_parameters": {
                "fault_onset_s": reference.fault_onset_s,
                "fault_rise_c_min": reference.fault_rise_c_min,
                "sensor_noise_c": reference.sensor_noise_c,
                "robot_speed_m_s": reference.robot_speed_m_s,
                "max_unseen_s": reference.max_unseen_s,
            },
            "method": (
                "Each policy receives the same seeded scenario. Time saved is "
                "reported only when both policies confirm the hotspot."
            ),
        },
        "summary": {
            "time_saved_s": _stats(
                case["time_saved_s"]
                for case in cases
                if case["time_saved_s"] is not None
            ),
            "adaptive_wins": {
                "count": adaptive_wins,
                "evaluated_count": evaluated,
                "rate_pct": _rate(adaptive_wins, evaluated),
            },
            "fixed_wins": {
                "count": fixed_wins,
                "evaluated_count": evaluated,
                "rate_pct": _rate(fixed_wins, evaluated),
            },
            "outcomes": outcome_counts,
            "confirmation_rate_pct": {
                "fixed": _rate(fixed_confirmations, total),
                "adaptive": _rate(adaptive_confirmations, total),
            },
            "coverage_pct": {
                "fixed": _stats(float(case["fixed"]["coverage_pct"]) for case in cases),
                "adaptive": _stats(float(case["adaptive"]["coverage_pct"]) for case in cases),
                "both_full_count": both_full_coverage,
                "both_full_rate_pct": _rate(both_full_coverage, total),
            },
            "distance_m": {
                "fixed": _stats(float(case["fixed"]["distance_m"]) for case in cases),
                "adaptive": _stats(float(case["adaptive"]["distance_m"]) for case in cases),
                "adaptive_minus_fixed": _stats(
                    float(case["distance_delta_m"]) for case in cases
                ),
            },
            "guardrails_respected": {
                "fixed_count": sum(case["fixed"]["guardrails_respected"] for case in cases),
                "adaptive_count": sum(
                    case["adaptive"]["guardrails_respected"] for case in cases
                ),
                "scenario_count": total,
            },
        },
        "scenarios": cases,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic 24-scenario Thermal Sentinel benchmark."
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit single-line JSON instead of indented JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    json.dump(
        run_benchmark(),
        sys.stdout,
        indent=None if args.compact else 2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
