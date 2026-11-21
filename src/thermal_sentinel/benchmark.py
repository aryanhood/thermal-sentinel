"""Paired benchmark: every scenario is run under both policies and compared."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .model import DEFAULT_BAYS, make_scenario, seeded_rng
from .simulation import PairResult, PolicyConfig, run_pair

__all__ = ["BenchmarkReport", "describe", "run_benchmark"]

SCHEMA_VERSION = "2.0"
DEFAULT_SEEDS = 100
FAULT_CHOICES: tuple[str | None, ...] = (*[bay.id for bay in DEFAULT_BAYS], None)


def describe(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p90": None, "min": None, "max": None}
    ordered = sorted(values)
    p90_index = max(0, min(len(ordered) - 1, round(0.9 * (len(ordered) - 1))))
    return {
        "n": len(ordered),
        "mean": round(statistics.fmean(ordered), 2),
        "median": round(statistics.median(ordered), 2),
        "p90": round(ordered[p90_index], 2),
        "min": round(ordered[0], 2),
        "max": round(ordered[-1], 2),
    }


def bootstrap_ci(values: Sequence[float], *, resamples: int = 2000, seed: int = 0) -> tuple[float, float] | None:
    """Percentile bootstrap 95% confidence interval of the mean (seeded, reproducible)."""
    if len(values) < 2:
        return None
    rng = seeded_rng(seed, "bootstrap")
    n = len(values)
    means = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(resamples))
    return round(means[int(0.025 * resamples)], 2), round(means[int(0.975 * resamples) - 1], 2)


def _outcome(pair: PairResult) -> str:
    f = pair.fixed.metrics["time_to_confirm_s"]
    a = pair.adaptive.metrics["time_to_confirm_s"]
    if not pair.scenario.fault_bay_id:
        return "no_fault"
    if f is None and a is None:
        return "neither"
    if f is None:
        return "adaptive_only"
    if a is None:
        return "fixed_only"
    if a < f:
        return "adaptive_faster"
    if f < a:
        return "fixed_faster"
    return "tie"


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    data: dict[str, Any]

    @property
    def summary(self) -> dict[str, Any]:
        return self.data["summary"]  # type: ignore[no-any-return]


def run_benchmark(seeds: Iterable[int] | None = None, config: PolicyConfig | None = None) -> BenchmarkReport:
    seed_list = list(seeds) if seeds is not None else list(range(1, DEFAULT_SEEDS + 1))
    pairs = [run_pair(make_scenario(seed, bay), config) for seed in seed_list for bay in FAULT_CHOICES]

    cases: list[dict[str, Any]] = []
    for pair in pairs:
        cases.append(
            {
                "case_id": pair.scenario.case_id,
                "seed": pair.scenario.seed,
                "fault_bay": pair.scenario.fault_bay_id,
                "fault_onset_s": pair.scenario.fault_onset_s,
                "outcome": _outcome(pair),
                "time_saved_s": pair.time_saved_s,
                "fixed": _compact(pair.fixed.metrics),
                "adaptive": _compact(pair.adaptive.metrics),
            }
        )

    fault_cases = [c for c in cases if c["fault_bay"]]
    clean_cases = [c for c in cases if not c["fault_bay"]]
    hours = len(pairs) * pairs[0].scenario.horizon_s / 3600 if pairs else 0.0
    saved = [c["time_saved_s"] for c in fault_cases if c["time_saved_s"] is not None]

    def per_policy(policy: str) -> dict[str, Any]:
        latencies = [c[policy]["time_to_confirm_s"] for c in fault_cases if c[policy]["time_to_confirm_s"] is not None]
        all_runs = [c[policy] for c in cases]
        return {
            "detection_rate_pct": round(100 * len(latencies) / len(fault_cases), 1) if fault_cases else None,
            "time_to_confirm_s": describe(latencies),
            "distance_m": describe([r["distance_m"] for r in all_runs]),
            "false_alarms_per_hour": round(sum(r["false_alarms"] for r in all_runs) / hours, 2) if hours else None,
            "false_confirmations": sum(r["false_confirmations"] for r in all_runs),
            "guardrail_ok_pct": round(100 * sum(r["guardrail_ok"] for r in all_runs) / len(all_runs), 1),
            "max_unseen_s": describe([r["max_unseen_s"] for r in all_runs]),
        }

    outcomes = dict.fromkeys(
        ("adaptive_faster", "adaptive_only", "tie", "fixed_faster", "fixed_only", "neither"),
        0,
    )
    for c in fault_cases:
        outcomes[c["outcome"]] += 1
    overhead = [
        100 * (c["adaptive"]["distance_m"] - c["fixed"]["distance_m"]) / c["fixed"]["distance_m"]
        for c in cases
        if c["fixed"]["distance_m"]
    ]
    ci = bootstrap_ci(saved)
    summary = {
        "cases": len(cases),
        "fault_cases": len(fault_cases),
        "no_fault_cases": len(clean_cases),
        "simulated_hours": round(hours, 2),
        "fixed": per_policy("fixed"),
        "adaptive": per_policy("adaptive"),
        "paired": {
            "outcomes": outcomes,
            "time_saved_s": describe(saved),
            "time_saved_ci95_s": list(ci) if ci else None,
            "distance_overhead_pct": describe(overhead),
        },
    }
    method = {
        "seeds": [seed_list[0], seed_list[-1]] if seed_list else [],
        "fault_choices": [bay or "none" for bay in FAULT_CHOICES],
        "fault_onset": "uniform 10-70 s, drawn per seed",
        "horizon_s": pairs[0].scenario.horizon_s if pairs else None,
        "pairing": (
            "both policies share one Environment: identical baselines, line-sensor noise, glitches and camera noise"
        ),
    }
    return BenchmarkReport({"schema_version": SCHEMA_VERSION, "method": method, "summary": summary, "cases": cases})


def _compact(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "detected",
        "time_to_confirm_s",
        "distance_m",
        "close_inspections",
        "false_alarms",
        "false_confirmations",
        "max_unseen_s",
        "guardrail_ok",
    )
    return {key: metrics[key] for key in keys}
