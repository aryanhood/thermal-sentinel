"""Command-line interface: ``thermal-sentinel {run,benchmark,export,serve}``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from . import __version__
from .api.app import create_app
from .benchmark import run_benchmark
from .export import check_export, export
from .model import DEFAULT_BAYS, make_scenario
from .simulation import Policy, run_pair

BAY_CHOICES = [bay.id for bay in DEFAULT_BAYS] + ["none"]
POLICY_CHOICES = [policy.value for policy in Policy]


def _cmd_run(args: argparse.Namespace) -> int:
    fault = None if args.fault == "none" else args.fault
    pair = run_pair(make_scenario(args.seed, fault))
    if args.json:
        print(
            json.dumps(
                {"fixed": pair.fixed.metrics, "adaptive": pair.adaptive.metrics, "time_saved_s": pair.time_saved_s},
                indent=2,
            )
        )
        return 0
    rows = [
        ("time to confirm (s)", "time_to_confirm_s"),
        ("distance (m)", "distance_m"),
        ("close inspections", "close_inspections"),
        ("false alarms", "false_alarms"),
        ("worst coverage gap (s)", "max_unseen_s"),
    ]
    print(f"{pair.scenario.case_id}  fault onset {pair.scenario.fault_onset_s:g} s\n")
    print(f"{'metric':<24}{'fixed':>10}{'adaptive':>10}")
    for label, key in rows:
        f, a = pair.fixed.metrics[key], pair.adaptive.metrics[key]
        print(f"{label:<24}{'—' if f is None else f:>10}{'—' if a is None else a:>10}")
    if pair.time_saved_s is not None:
        print(f"\nadaptive confirmed {pair.time_saved_s:+g} s relative to fixed")
    return 0


def _cmd_benchmark(args: argparse.Namespace) -> int:
    report = run_benchmark(range(1, args.seeds + 1))
    if args.json:
        print(json.dumps(report.data if args.cases else report.summary, indent=2, sort_keys=True))
        return 0
    s = report.summary
    f, a, p = s["fixed"], s["adaptive"], s["paired"]
    print(f"{s['cases']} paired cases ({s['fault_cases']} faulted, {s['no_fault_cases']} clean)")
    policies = s.get("policies", {"fixed": f, "adaptive": a})
    for key in ("fixed", "greedy_ewma", "adaptive", "cusum", "bayesian"):
        if key not in policies:
            continue
        pm = policies[key]
        print(
            f"  {key:<12} detect={pm['detection_rate_pct']}%  med_ttc={pm['time_to_confirm_s']['median']}s  "
            f"p90={pm['time_to_confirm_s']['p90']}s  fa/hr={pm['false_alarms_per_hour']}  "
            f"guard={pm['guardrail_ok_pct']}%"
        )
    print(f"mean time saved (adaptive vs fixed): {p['time_saved_s']['mean']} s, 95% CI {p['time_saved_ci95_s']}")
    print(f"distance overhead: {p['distance_overhead_pct']['mean']}% mean")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if args.check:
        problems = check_export(root)
        if problems:
            print("Generated files are out of date. Run `thermal-sentinel export`:", file=sys.stderr)
            for item in problems:
                print(f"  {item}", file=sys.stderr)
            return 1
        print("Generated files are up to date.")
        return 0
    written = export(root)
    print(f"Wrote {len(written)} files under {root.resolve()}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    application = create_app(db_path=args.db, policy=args.policy)
    uvicorn.run(application, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="thermal-sentinel", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one paired scenario")
    run.add_argument("--seed", type=int, default=1)
    run.add_argument("--fault", choices=BAY_CHOICES, default="B5")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=_cmd_run)

    bench = sub.add_parser("benchmark", help="run the paired benchmark")
    bench.add_argument("--seeds", type=int, default=100)
    bench.add_argument("--json", action="store_true")
    bench.add_argument("--cases", action="store_true", help="include per-case rows in JSON output")
    bench.set_defaults(func=_cmd_benchmark)

    exp = sub.add_parser("export", help="regenerate web data and docs/benchmark.md")
    exp.add_argument("--root", default=".", help="repository root (default: current directory)")
    exp.add_argument("--check", action="store_true", help="fail if generated files are stale")
    exp.set_defaults(func=_cmd_export)

    srv = sub.add_parser("serve", help="start the FastAPI monitoring server")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.add_argument("--db", default="thermal_sentinel.db", help="SQLite database path")
    srv.add_argument("--policy", choices=POLICY_CHOICES, default=Policy.BAYESIAN.value)
    srv.set_defaults(func=_cmd_serve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "seeds", 1) < 1:
        print("--seeds must be at least 1", file=sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
