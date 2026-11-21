from __future__ import annotations

import pytest

from thermal_sentinel.benchmark import bootstrap_ci, describe, run_benchmark


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    return run_benchmark(range(1, 11)).data


def test_describe_empty_and_basic() -> None:
    assert describe([])["n"] == 0
    stats = describe([3.0, 1.0, 2.0])
    assert stats == {"n": 3, "mean": 2.0, "median": 2.0, "p90": 3.0, "min": 1.0, "max": 3.0}


def test_bootstrap_ci_is_reproducible_and_brackets_mean() -> None:
    values = [float(v) for v in range(20)]
    ci = bootstrap_ci(values)
    assert ci == bootstrap_ci(values)
    assert ci is not None
    assert ci[0] < 9.5 < ci[1]
    assert bootstrap_ci([1.0]) is None


def test_case_counts(report: dict[str, object]) -> None:
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert summary["cases"] == 70
    assert summary["fault_cases"] == 60
    assert summary["no_fault_cases"] == 10
    assert sum(summary["paired"]["outcomes"].values()) == 60


def test_adaptive_beats_fixed_on_median_latency(report: dict[str, object]) -> None:
    summary = report["summary"]
    assert isinstance(summary, dict)
    assert summary["adaptive"]["time_to_confirm_s"]["median"] < summary["fixed"]["time_to_confirm_s"]["median"]


def test_benchmark_is_deterministic(report: dict[str, object]) -> None:
    assert run_benchmark(range(1, 11)).data == report


def test_no_fault_cases_have_no_outcome_entry(report: dict[str, object]) -> None:
    cases = report["cases"]
    assert isinstance(cases, list)
    assert all(c["outcome"] == "no_fault" for c in cases if c["fault_bay"] is None)
