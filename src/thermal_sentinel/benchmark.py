"""Paired benchmark: every scenario is run under all policies on a shared environment."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .environment import Environment
from .model import DEFAULT_BAYS, make_scenario, seeded_rng
from .simulation import Policy, PolicyConfig, RunResult, Simulation

__all__ = [
    "ALL_POLICIES",
    "GLITCH_PROBS",
    "RISE_RATES_C_PER_MIN",
    "BenchmarkReport",
    "describe",
    "run_benchmark",
    "run_sensitivity_sweep",
]

SCHEMA_VERSION = "2.1"
DEFAULT_SEEDS = 100
DEFAULT_SWEEP_SEEDS = 15
ALL_POLICIES: tuple[Policy, ...] = (
    Policy.FIXED,
    Policy.GREEDY_EWMA,
    Policy.ADAPTIVE,
    Policy.CUSUM,
    Policy.BAYESIAN,
)
FAULT_CHOICES: tuple[str | None, ...] = (*[bay.id for bay in DEFAULT_BAYS], None)
FAULT_BAYS_ONLY: tuple[str, ...] = tuple(bay.id for bay in DEFAULT_BAYS)
GLITCH_PROBS: tuple[float, ...] = (0.0, 0.0005, 0.002, 0.005)
RISE_RATES_C_PER_MIN: tuple[float, ...] = (6.0, 12.0, 24.0)


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


def _outcome(fixed_ttc: float | None, adaptive_ttc: float | None, fault_bay_id: str | None) -> str:
    if not fault_bay_id:
        return "no_fault"
    if fixed_ttc is None and adaptive_ttc is None:
        return "neither"
    if fixed_ttc is None:
        return "adaptive_only"
    if adaptive_ttc is None:
        return "fixed_only"
    if adaptive_ttc < fixed_ttc:
        return "adaptive_faster"
    if fixed_ttc < adaptive_ttc:
        return "fixed_faster"
    return "tie"


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    data: dict[str, Any]

    @property
    def summary(self) -> dict[str, Any]:
        return self.data["summary"]  # type: ignore[no-any-return]


def _run_all_on_scenario(
    scenario: Any,
    config: PolicyConfig | None,
) -> dict[Policy, RunResult]:
    env = Environment(scenario)
    return {pol: Simulation(env, pol, config).run() for pol in ALL_POLICIES}


def run_sensitivity_sweep(
    seeds: Iterable[int] | None = None,
    config: PolicyConfig | None = None,
) -> list[dict[str, Any]]:
    """Run 2D sensitivity sweep across glitch rate and fault ramp rate on shared environments."""
    seed_list = list(seeds) if seeds is not None else list(range(1, DEFAULT_SWEEP_SEEDS + 1))
    grid_rows: list[dict[str, Any]] = []
    for glitch_prob in GLITCH_PROBS:
        for rise_rate in RISE_RATES_C_PER_MIN:
            per_pol_lat: dict[str, list[float]] = {p.value: [] for p in ALL_POLICIES}
            per_pol_fa: dict[str, int] = {p.value: 0 for p in ALL_POLICIES}
            per_pol_fc: dict[str, int] = {p.value: 0 for p in ALL_POLICIES}
            per_pol_dist: dict[str, list[float]] = {p.value: [] for p in ALL_POLICIES}
            per_pol_guard: dict[str, int] = {p.value: 0 for p in ALL_POLICIES}
            n_cases = 0
            horizon_s = 180.0
            for seed in seed_list:
                for bay_id in FAULT_BAYS_ONLY:
                    sc = make_scenario(
                        seed,
                        bay_id,
                        line_glitch_prob=glitch_prob,
                        fault_rise_c_per_min=rise_rate,
                    )
                    horizon_s = sc.horizon_s
                    n_cases += 1
                    runs = _run_all_on_scenario(sc, config)
                    for pol, res in runs.items():
                        m = res.metrics
                        ttc = m["time_to_confirm_s"]
                        if ttc is not None:
                            per_pol_lat[pol.value].append(float(ttc))
                        per_pol_fa[pol.value] += int(m["false_alarms"])
                        per_pol_fc[pol.value] += int(m["false_confirmations"])
                        per_pol_dist[pol.value].append(float(m["distance_m"]))
                        per_pol_guard[pol.value] += int(bool(m["guardrail_ok"]))

            hours = (n_cases * horizon_s / 3600.0) if n_cases else 0.0
            policies_cell: dict[str, Any] = {}
            for pol in ALL_POLICIES:
                pv = pol.value
                lats = per_pol_lat[pv]
                lat_stats = describe(lats)
                policies_cell[pv] = {
                    "detection_rate_pct": round(100.0 * len(lats) / n_cases, 1) if n_cases else None,
                    "ttc_median_s": lat_stats["median"],
                    "ttc_mean_s": lat_stats["mean"],
                    "ttc_p90_s": lat_stats["p90"],
                    "false_alarms_per_hour": round(per_pol_fa[pv] / hours, 2) if hours else None,
                    "false_confirmations": per_pol_fc[pv],
                    "distance_mean_m": round(statistics.fmean(per_pol_dist[pv]), 2) if per_pol_dist[pv] else None,
                    "guardrail_ok_pct": round(100.0 * per_pol_guard[pv] / n_cases, 1) if n_cases else 100.0,
                }
            grid_rows.append(
                {
                    "line_glitch_prob": glitch_prob,
                    "fault_rise_c_per_min": rise_rate,
                    "fault_cases": n_cases,
                    "simulated_hours": round(hours, 2),
                    "policies": policies_cell,
                }
            )
    return grid_rows


def run_benchmark(
    seeds: Iterable[int] | None = None,
    config: PolicyConfig | None = None,
    sweep_seeds: Iterable[int] | None = None,
) -> BenchmarkReport:
    seed_list = list(seeds) if seeds is not None else list(range(1, DEFAULT_SEEDS + 1))
    sw_list = (
        list(sweep_seeds)
        if sweep_seeds is not None
        else (list(range(1, DEFAULT_SWEEP_SEEDS + 1)) if seeds is None else seed_list[:3])
    )

    cases: list[dict[str, Any]] = []
    horizon_s = 180.0
    for seed in seed_list:
        for bay in FAULT_CHOICES:
            sc = make_scenario(seed, bay)
            horizon_s = sc.horizon_s
            runs = _run_all_on_scenario(sc, config)
            f_m = runs[Policy.FIXED].metrics
            a_m = runs[Policy.ADAPTIVE].metrics
            f_ttc = f_m["time_to_confirm_s"]
            a_ttc = a_m["time_to_confirm_s"]
            saved = None if f_ttc is None or a_ttc is None else round(f_ttc - a_ttc, 2)
            row: dict[str, Any] = {
                "case_id": sc.case_id,
                "seed": sc.seed,
                "fault_bay": sc.fault_bay_id,
                "fault_onset_s": sc.fault_onset_s,
                "outcome": _outcome(f_ttc, a_ttc, sc.fault_bay_id),
                "time_saved_s": saved,
                "fixed": _compact(f_m),
                "adaptive": _compact(a_m),
                "policies": {pol.value: _compact(res.metrics) for pol, res in runs.items()},
            }
            cases.append(row)

    fault_cases = [c for c in cases if c["fault_bay"]]
    clean_cases = [c for c in cases if not c["fault_bay"]]
    hours = len(cases) * horizon_s / 3600.0 if cases else 0.0
    saved_vals = [c["time_saved_s"] for c in fault_cases if c["time_saved_s"] is not None]

    def per_policy(policy_name: str, *, include_saved: bool = False) -> dict[str, Any]:
        latencies = [
            c["policies"][policy_name]["time_to_confirm_s"]
            for c in fault_cases
            if c["policies"][policy_name]["time_to_confirm_s"] is not None
        ]
        all_runs = [c["policies"][policy_name] for c in cases]
        res: dict[str, Any] = {
            "detection_rate_pct": round(100 * len(latencies) / len(fault_cases), 1) if fault_cases else None,
            "time_to_confirm_s": describe(latencies),
            "distance_m": describe([r["distance_m"] for r in all_runs]),
            "false_alarms_per_hour": round(sum(r["false_alarms"] for r in all_runs) / hours, 2) if hours else None,
            "false_confirmations": sum(r["false_confirmations"] for r in all_runs),
            "guardrail_ok_pct": round(100 * sum(r["guardrail_ok"] for r in all_runs) / len(all_runs), 1)
            if all_runs
            else 100.0,
            "max_unseen_s": describe([r["max_unseen_s"] for r in all_runs]),
        }
        if include_saved:
            diffs = [
                round(c["fixed"]["time_to_confirm_s"] - c["policies"][policy_name]["time_to_confirm_s"], 2)
                for c in fault_cases
                if c["fixed"]["time_to_confirm_s"] is not None
                and c["policies"][policy_name]["time_to_confirm_s"] is not None
            ]
            ci_pol = bootstrap_ci(diffs)
            res["mean_time_saved_vs_fixed_s"] = describe(diffs)["mean"]
            res["time_saved_ci95_s"] = list(ci_pol) if ci_pol else None
        return res

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
    ci = bootstrap_ci(saved_vals)
    policies_summary = {pol.value: per_policy(pol.value, include_saved=True) for pol in ALL_POLICIES}

    summary = {
        "cases": len(cases),
        "fault_cases": len(fault_cases),
        "no_fault_cases": len(clean_cases),
        "simulated_hours": round(hours, 2),
        "fixed": per_policy("fixed"),
        "adaptive": per_policy("adaptive"),
        "policies": policies_summary,
        "paired": {
            "outcomes": outcomes,
            "time_saved_s": describe(saved_vals),
            "time_saved_ci95_s": list(ci) if ci else None,
            "distance_overhead_pct": describe(overhead),
        },
    }
    method = {
        "seeds": [seed_list[0], seed_list[-1]] if seed_list else [],
        "fault_choices": [bay or "none" for bay in FAULT_CHOICES],
        "fault_onset": "uniform 10-70 s, drawn per seed",
        "horizon_s": horizon_s if cases else None,
        "pairing": (
            "all policies share one Environment: identical baselines, line-sensor noise, glitches and camera noise"
        ),
    }
    sweep = run_sensitivity_sweep(seeds=sw_list, config=config)
    return BenchmarkReport(
        {
            "schema_version": SCHEMA_VERSION,
            "method": method,
            "summary": summary,
            "sensitivity_sweep": sweep,
            "cases": cases,
        }
    )


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
