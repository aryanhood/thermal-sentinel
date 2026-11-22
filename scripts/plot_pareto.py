"""Generate Pareto and ramp-rate sensitivity figure from exported benchmark JSON."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt


def main() -> None:
    plt.switch_backend("Agg")
    root = Path(__file__).resolve().parents[1]
    bench_path = root / "web" / "public" / "data" / "benchmark.json"
    out_path = root / "assets" / "pareto_sensitivity.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = json.loads(bench_path.read_text(encoding="utf-8"))
    sweep = report["sensitivity_sweep"]

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.4), dpi=300)

    # Panel A: Latency vs False Alarms/hr across glitch rates at nominal 12 C/min ramp
    nominal_rows = [r for r in sweep if abs(r["fault_rise_c_per_min"] - 12.0) < 1e-6]
    styles = [
        ("fixed", "Fixed Patrol", "#64748b", "s", "--"),
        ("greedy_ewma", "Greedy EWMA", "#f97316", "^", ":"),
        ("adaptive", "Risk-Adaptive EWMA", "#0284c7", "o", "-"),
        ("cusum", "Page CUSUM", "#7c3aed", "D", "-"),
        ("bayesian", "3-State Bayesian HMM", "#059669", "*", "-"),
    ]
    for key, label, color, marker, ls in styles:
        xs = [r["policies"][key]["false_alarms_per_hour"] for r in nominal_rows]
        ys = [r["policies"][key]["ttc_median_s"] for r in nominal_rows]
        ms = 11 if marker == "*" else 6.5
        ax1.plot(xs, ys, label=label, color=color, marker=marker, markersize=ms, linestyle=ls, linewidth=1.8)
    for r in nominal_rows:
        if r["line_glitch_prob"] in (0.0005, 0.005):
            px = r["policies"]["adaptive"]["false_alarms_per_hour"]
            py = r["policies"]["adaptive"]["ttc_median_s"]
            ax1.annotate(
                f"p={r['line_glitch_prob']}",
                xy=(px, py),
                xytext=(-24, 9),
                textcoords="offset points",
                fontsize=8,
                color="#0369a1",
            )
    ax1.set_title("(a) Latency vs. False Alarms (12 °C/min Ramp)")
    ax1.set_xlabel("False-Alarm Detours per Simulated Hour")
    ax1.set_ylabel("Median Time to Confirm (s)")
    ax1.grid(True, linestyle=":", alpha=0.45)
    ax1.legend(loc="upper right", fontsize=8.5, frameon=True)

    # Panel B: Median TTC across ramp rates (6, 12, 24 C/min) at nominal glitch rate p=0.0005
    ramp_rows = sorted(
        [r for r in sweep if abs(r["line_glitch_prob"] - 0.0005) < 1e-9],
        key=lambda r: r["fault_rise_c_per_min"],
    )
    ramps = [r["fault_rise_c_per_min"] for r in ramp_rows]
    for key, label, color, marker, ls in styles:
        ys = [r["policies"][key]["ttc_median_s"] for r in ramp_rows]
        ms = 11 if marker == "*" else 6.5
        ax2.plot(ramps, ys, label=label, color=color, marker=marker, markersize=ms, linestyle=ls, linewidth=1.8)
    ax2.set_title("(b) Fault Ramp-Rate Sensitivity (p_glitch = 0.0005)")
    ax2.set_xlabel("Fault Ramp Rate (°C / min)")
    ax2.set_ylabel("Median Time to Confirm (s)")
    ax2.set_xticks(ramps)
    ax2.grid(True, linestyle=":", alpha=0.45)

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
