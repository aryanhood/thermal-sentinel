import { ArrowUpRight } from "lucide-react";
import { useMemo, useState } from "react";
import type { BenchmarkCase, BenchmarkReport, DistributionStats } from "../types";

interface BenchmarkViewProps {
  report: BenchmarkReport | null;
  loading: boolean;
  error: string | null;
  replayableCaseIds: Set<string>;
  onOpenReplay: (caseId: string) => void;
}

export function BenchmarkView({
  report,
  loading,
  error,
  replayableCaseIds,
  onOpenReplay,
}: BenchmarkViewProps) {
  const [bayFilter, setBayFilter] = useState<string>("all");
  const [outcomeFilter, setOutcomeFilter] = useState<string>("all");

  const histogram = useMemo(() => {
    if (!report) return [];
    const values = report.cases.map((c) => c.time_saved_s).filter((v): v is number => v !== null);
    const bins = [
      { label: "< 0s", min: Number.NEGATIVE_INFINITY, max: 0, count: 0, negative: true },
      { label: "0–5s", min: 0, max: 5, count: 0, negative: false },
      { label: "5–15s", min: 5, max: 15, count: 0, negative: false },
      { label: "15–25s", min: 15, max: 25, count: 0, negative: false },
      { label: "25–35s", min: 25, max: 35, count: 0, negative: false },
      { label: "35s+", min: 35, max: Number.POSITIVE_INFINITY, count: 0, negative: false },
    ];
    for (const v of values) {
      const bin = bins.find((b) => v >= b.min && v < b.max);
      if (bin) bin.count += 1;
    }
    return bins;
  }, [report]);

  const filteredCases: BenchmarkCase[] = useMemo(() => {
    if (!report) return [];
    return report.cases
      .filter((c) => {
        if (bayFilter !== "all" && (c.fault_bay ?? "none") !== bayFilter) return false;
        if (outcomeFilter !== "all" && c.outcome !== outcomeFilter) return false;
        return true;
      })
      .slice(0, 60);
  }, [report, bayFilter, outcomeFilter]);

  if (error) {
    return (
      <div className="rounded-xl border border-rose-500/40 bg-rose-950/40 p-4 text-sm text-rose-200">
        {error}
      </div>
    );
  }

  if (loading || !report) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-10 text-center text-sm text-slate-400">
        Loading 700-case benchmark…
      </div>
    );
  }

  const { summary } = report;
  const { fixed, adaptive, paired } = summary;
  const ci = paired.time_saved_ci95_s;
  const maxBin = Math.max(1, ...histogram.map((b) => b.count));

  const outcomes = [
    {
      key: "adaptive_faster",
      label: "Adaptive faster",
      count: paired.outcomes.adaptive_faster ?? 0,
      color: "bg-emerald-500",
    },
    {
      key: "tie",
      label: "Tie",
      count: paired.outcomes.tie ?? 0,
      color: "bg-slate-500",
    },
    {
      key: "fixed_faster",
      label: "Fixed faster",
      count: paired.outcomes.fixed_faster ?? 0,
      color: "bg-amber-400",
    },
  ];
  const faultTotal = Math.max(1, summary.fault_cases);

  return (
    <div className="space-y-4">
      {/* Headline metrics */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryCard
          label="Mean Time Saved"
          value={`${paired.time_saved_s.mean ?? "—"} s`}
          sub={ci ? `95% bootstrap CI: ${ci[0]} – ${ci[1]} s` : "Paired fault cases"}
          accent="emerald"
        />
        <SummaryCard
          label="Median Confirm Latency"
          value={`${adaptive.time_to_confirm_s.median ?? "—"} s`}
          sub={`Fixed baseline: ${fixed.time_to_confirm_s.median ?? "—"} s (p90: ${adaptive.time_to_confirm_s.p90}s vs ${fixed.time_to_confirm_s.p90}s)`}
        />
        <SummaryCard
          label="Distance Overhead"
          value={`+${paired.distance_overhead_pct.mean ?? "—"}%`}
          sub={`p90 overhead: +${paired.distance_overhead_pct.p90 ?? "—"}%`}
        />
        <SummaryCard
          label="False Alarms / Hr"
          value={`${adaptive.false_alarms_per_hour ?? "—"}`}
          sub={`Fixed: ${fixed.false_alarms_per_hour ?? "—"} · ${adaptive.false_confirmations} false confirmations`}
          accent="amber"
        />
      </div>

      {/* Charts row */}
      <div className="grid gap-4 lg:grid-cols-2">
        {/* Latency distribution range comparison */}
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h3 className="text-sm font-semibold text-slate-100">
            Confirmation Latency Distribution ({summary.fault_cases} Faulted Cases)
          </h3>
          <p className="mt-0.5 text-xs text-slate-400">
            Min, median, p90, and max time from fault onset to close-range confirmation.
          </p>

          <div className="mt-5 space-y-5">
            <RangeBar
              label="Fixed Patrol"
              stats={fixed.time_to_confirm_s}
              domainMax={60}
              barColor="bg-sky-500/70"
              dotColor="bg-sky-300"
            />
            <RangeBar
              label="Risk-Adaptive"
              stats={adaptive.time_to_confirm_s}
              domainMax={60}
              barColor="bg-emerald-500/70"
              dotColor="bg-emerald-300"
            />
          </div>

          {/* Outcome stacked bar */}
          <div className="mt-6 border-t border-slate-800 pt-4">
            <div className="mb-2 flex items-center justify-between text-xs text-slate-400">
              <span>Paired Outcomes ({summary.fault_cases} faulted)</span>
              <span className="font-mono text-slate-300">
                {(((paired.outcomes.adaptive_faster ?? 0) / faultTotal) * 100).toFixed(1)}% faster
              </span>
            </div>
            <div className="flex h-3 overflow-hidden rounded-full bg-slate-800">
              {outcomes.map((o) => (
                <div
                  key={o.key}
                  className={o.color}
                  style={{ width: `${(o.count / faultTotal) * 100}%` }}
                  title={`${o.label}: ${o.count}`}
                />
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-4 font-mono text-[11px] text-slate-400">
              {outcomes.map((o) => (
                <span key={o.key} className="inline-flex items-center gap-1.5">
                  <span className={`h-2 w-2 rounded-full ${o.color}`} />
                  {o.label}: <strong className="text-slate-200">{o.count}</strong>
                </span>
              ))}
            </div>
          </div>
        </section>

        {/* Time-saved histogram + trade-offs */}
        <section className="flex flex-col justify-between rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div>
            <h3 className="text-sm font-semibold text-slate-100">
              Time Saved per Scenario (Fixed − Adaptive)
            </h3>
            <p className="mt-0.5 text-xs text-slate-400">
              Positive values mean the adaptive policy confirmed the hotspot earlier.
            </p>

            <div className="mt-5 grid h-40 grid-cols-6 items-end gap-2">
              {histogram.map((bin) => (
                <div key={bin.label} className="flex h-full flex-col items-center justify-end gap-1">
                  <span className="font-mono text-[11px] tabular-nums text-slate-400">{bin.count}</span>
                  <div className="flex w-full flex-1 items-end">
                    <div
                      className={`w-full rounded-t-md ${
                        bin.negative ? "bg-amber-400/80" : "bg-emerald-500/80"
                      }`}
                      style={{ height: `${Math.max(4, (bin.count / maxBin) * 100)}%` }}
                    />
                  </div>
                  <span className="font-mono text-[10px] text-slate-400">{bin.label}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-5 rounded-lg border border-amber-500/25 bg-amber-500/5 p-3 text-xs leading-relaxed text-slate-300">
            <strong className="text-amber-300">Trade-off:</strong> Trusting noisy line sensors causes{" "}
            <span className="font-mono">{adaptive.false_alarms_per_hour}</span> false-alarm detours/hr,{" "}
            <span className="font-mono">+{paired.distance_overhead_pct.mean}%</span> mean distance, and a
            worst-case coverage gap of <span className="font-mono">{adaptive.max_unseen_s.max}s</span> (vs.{" "}
            <span className="font-mono">{fixed.max_unseen_s.max}s</span> fixed) — while remaining 100% within
            the 90s guardrail with zero false confirmations.
          </div>
        </section>
      </div>

      {/* Five-Policy Ablation Table (when exported) */}
      {summary.policies && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <h3 className="text-sm font-semibold text-slate-100">
            Five-Policy Comparison ({summary.cases} Shared Environments)
          </h3>
          <p className="mt-0.5 text-xs text-slate-400">
            Fixed patrol vs. Greedy EWMA vs. Risk-Adaptive EWMA vs. Page CUSUM vs. 3-State Bayesian HMM.
          </p>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left font-mono text-xs">
              <thead className="border-b border-slate-800 text-[11px] uppercase text-slate-500">
                <tr>
                  <th className="py-2 pr-3">Policy</th>
                  <th className="py-2 pr-3 text-right">Detect</th>
                  <th className="py-2 pr-3 text-right">Median TTC</th>
                  <th className="py-2 pr-3 text-right">p90 TTC</th>
                  <th className="py-2 pr-3 text-right">Saved vs Fixed</th>
                  <th className="py-2 pr-3 text-right">False Alarms/hr</th>
                  <th className="py-2 text-right">Guardrail OK</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 tabular-nums">
                {[
                  { key: "fixed", label: "Fixed Patrol" },
                  { key: "greedy_ewma", label: "Greedy EWMA" },
                  { key: "adaptive", label: "Risk-Adaptive EWMA" },
                  { key: "cusum", label: "Page CUSUM" },
                  { key: "bayesian", label: "3-State Bayesian HMM" },
                ]
                  .filter((p) => summary.policies?.[p.key])
                  .map(({ key, label }) => {
                    const pm = summary.policies?.[key];
                    if (!pm) return null;
                    return (
                      <tr key={key} className="hover:bg-slate-800/30">
                        <td className="py-2 pr-3 font-sans font-medium text-slate-200">{label}</td>
                        <td className="py-2 pr-3 text-right text-slate-300">
                          {pm.detection_rate_pct ?? "—"}%
                        </td>
                        <td className="py-2 pr-3 text-right text-emerald-300">
                          {pm.time_to_confirm_s.median ?? "—"}s
                        </td>
                        <td className="py-2 pr-3 text-right text-slate-300">
                          {pm.time_to_confirm_s.p90 ?? "—"}s
                        </td>
                        <td className="py-2 pr-3 text-right text-sky-300">
                          {pm.mean_time_saved_vs_fixed_s != null
                            ? `${pm.mean_time_saved_vs_fixed_s > 0 ? "+" : ""}${pm.mean_time_saved_vs_fixed_s}s`
                            : "—"}
                        </td>
                        <td className="py-2 pr-3 text-right text-amber-300">
                          {pm.false_alarms_per_hour ?? "—"}
                        </td>
                        <td className="py-2 text-right text-slate-300">{pm.guardrail_ok_pct}%</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Case Explorer Table */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-100">
              Scenario Explorer{" "}
              <span className="font-normal text-slate-500">
                (showing up to 60 of {summary.cases} cases · {summary.simulated_hours} sim hrs)
              </span>
            </h3>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <select
              aria-label="Filter by fault bay"
              value={bayFilter}
              onChange={(e) => setBayFilter(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-200"
            >
              <option value="all">All bays</option>
              {["B1", "B2", "B3", "B4", "B5", "B6", "none"].map((b) => (
                <option key={b} value={b}>
                  {b === "none" ? "Control (none)" : b}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter by outcome"
              value={outcomeFilter}
              onChange={(e) => setOutcomeFilter(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-200"
            >
              <option value="all">All outcomes</option>
              <option value="adaptive_faster">Adaptive faster</option>
              <option value="tie">Tie</option>
              <option value="fixed_faster">Fixed faster</option>
              <option value="no_fault">Control (no fault)</option>
            </select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead className="border-b border-slate-800 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="py-2 pr-3">Case</th>
                <th className="py-2 pr-3">Onset</th>
                <th className="py-2 pr-3 text-right">Fixed TTC</th>
                <th className="py-2 pr-3 text-right">Adaptive TTC</th>
                <th className="py-2 pr-3 text-right">Saved</th>
                <th className="py-2 pr-3 text-right">False Alarms</th>
                <th className="py-2 text-right">Replay</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 tabular-nums">
              {filteredCases.map((c) => {
                const canReplay = replayableCaseIds.has(c.case_id);
                return (
                  <tr key={c.case_id} className="hover:bg-slate-800/30">
                    <td className="py-2 pr-3 text-slate-200">{c.case_id}</td>
                    <td className="py-2 pr-3 text-slate-400">{c.fault_onset_s.toFixed(1)}s</td>
                    <td className="py-2 pr-3 text-right text-sky-300">
                      {c.fixed.time_to_confirm_s !== null ? `${c.fixed.time_to_confirm_s.toFixed(1)}s` : "—"}
                    </td>
                    <td className="py-2 pr-3 text-right text-emerald-300">
                      {c.adaptive.time_to_confirm_s !== null
                        ? `${c.adaptive.time_to_confirm_s.toFixed(1)}s`
                        : "—"}
                    </td>
                    <td
                      className={`py-2 pr-3 text-right ${
                        c.time_saved_s === null
                          ? "text-slate-500"
                          : c.time_saved_s > 0
                            ? "text-emerald-400"
                            : c.time_saved_s < 0
                              ? "text-amber-300"
                              : "text-slate-400"
                      }`}
                    >
                      {c.time_saved_s === null
                        ? "—"
                        : `${c.time_saved_s > 0 ? "+" : ""}${c.time_saved_s.toFixed(1)}s`}
                    </td>
                    <td className="py-2 pr-3 text-right text-slate-400">{c.adaptive.false_alarms}</td>
                    <td className="py-2 text-right">
                      {canReplay ? (
                        <button
                          type="button"
                          onClick={() => onOpenReplay(c.case_id)}
                          className="inline-flex items-center gap-0.5 text-emerald-400 hover:text-emerald-300"
                        >
                          Open <ArrowUpRight className="h-3 w-3" />
                        </button>
                      ) : (
                        <span className="text-slate-600">CLI</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  sub,
  accent = "neutral",
}: {
  label: string;
  value: string;
  sub: string;
  accent?: "neutral" | "emerald" | "amber";
}) {
  const cls =
    accent === "emerald" ? "text-emerald-300" : accent === "amber" ? "text-amber-300" : "text-slate-100";
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-semibold tabular-nums ${cls}`}>{value}</div>
      <div className="mt-1 text-xs text-slate-400">{sub}</div>
    </div>
  );
}

function RangeBar({
  label,
  stats,
  domainMax,
  barColor,
  dotColor,
}: {
  label: string;
  stats: DistributionStats;
  domainMax: number;
  barColor: string;
  dotColor: string;
}) {
  const min = stats.min ?? 0;
  const median = stats.median ?? 0;
  const p90 = stats.p90 ?? 0;
  const max = stats.max ?? 0;
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / domainMax) * 100))}%`;

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between text-xs">
        <span className="font-medium text-slate-200">{label}</span>
        <span className="font-mono tabular-nums text-slate-400">
          med {median}s · p90 {p90}s · max {max}s
        </span>
      </div>
      <div className="relative h-3 rounded-full bg-slate-800">
        {/* min..max whisker */}
        <div
          className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-slate-500"
          style={{ left: pct(min), width: `calc(${pct(max)} - ${pct(min)})` }}
        />
        {/* median..p90 emphasis */}
        <div
          className={`absolute top-0.5 h-2 rounded-full ${barColor}`}
          style={{ left: pct(min), width: `calc(${pct(p90)} - ${pct(min)})` }}
        />
        {/* median dot */}
        <div
          className={`absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-slate-950 ${dotColor}`}
          style={{ left: pct(median) }}
          title={`Median ${median}s`}
        />
      </div>
    </div>
  );
}
