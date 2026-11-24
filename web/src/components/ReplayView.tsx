import { Eye, EyeOff, FastForward, Pause, Play, RotateCcw, SkipBack, SkipForward } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { CatalogEntry, PlannerDecision, ScenarioDocument, SimEvent } from "../types";
import { FloorMap } from "./FloorMap";

interface ReplayViewProps {
  catalog: CatalogEntry[];
  caseId: string;
  onSelectCase: (caseId: string) => void;
  doc: ScenarioDocument | null;
  loading: boolean;
  error: string | null;
}

const SPEEDS = [1, 2, 4, 8] as const;

const EVENT_COLORS: Record<string, string> = {
  confirmed: "text-rose-300 bg-rose-500/15 border-rose-500/30",
  retarget: "text-amber-300 bg-amber-500/15 border-amber-500/30",
  line_alert: "text-amber-200 bg-amber-500/10 border-amber-500/20",
  false_alarm: "text-orange-300 bg-orange-500/15 border-orange-500/30",
  second_look: "text-violet-300 bg-violet-500/15 border-violet-500/30",
  dwell: "text-violet-200 bg-violet-500/10 border-violet-500/20",
};

function latestDecision(decisions: PlannerDecision[], t: number): PlannerDecision | null {
  let found: PlannerDecision | null = null;
  for (const d of decisions) {
    if (d.t <= t) {
      found = d;
    } else {
      break;
    }
  }
  return found;
}

export function ReplayView({ catalog, caseId, onSelectCase, doc, loading, error }: ReplayViewProps) {
  const [tick, setTick] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(4);
  const [showGroundTruth, setShowGroundTruth] = useState(true);
  const [eventFilter, setEventFilter] = useState<"adaptive" | "fixed">("adaptive");

  const maxTick = doc ? Math.max(0, doc.adaptive.frames.t.length - 1) : 0;
  const safeTick = Math.max(0, Math.min(tick, maxTick));

  // Reset playback when scenario changes
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentionally reset on caseId change
  useEffect(() => {
    setTick(0);
    setPlaying(true);
  }, [caseId]);

  useEffect(() => {
    if (playing && maxTick > 0 && tick >= maxTick) {
      setPlaying(false);
    }
  }, [playing, maxTick, tick]);

  useEffect(() => {
    if (!playing || !doc || maxTick === 0) return;
    const intervalMs = Math.max(25, Math.round((doc.scenario.dt_s * 1000) / speed));
    const id = window.setInterval(() => {
      setTick((prev) => (prev >= maxTick ? maxTick : prev + 1));
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [playing, doc, maxTick, speed]);

  const seeds = useMemo(
    () => Array.from(new Set(catalog.map((c) => c.seed))).sort((a, b) => a - b),
    [catalog],
  );
  const selectedEntry = catalog.find((c) => c.case_id === caseId);
  const currentSeed = selectedEntry?.seed ?? seeds[0] ?? 1;
  const currentBay = selectedEntry?.fault_bay ?? "none";

  const handleSeedOrBay = (seed: number, bay: string) => {
    const match = catalog.find((c) => c.seed === seed && (c.fault_bay ?? "none") === bay);
    if (match) onSelectCase(match.case_id);
  };

  const currentTime = doc?.adaptive.frames.t[safeTick] ?? 0;

  const markerLeft = (t: number) => {
    const horizon = doc?.scenario.horizon_s ?? 1;
    const pct = horizon > 0 ? (t / horizon) * 100 : 0;
    return `${Math.min(100, Math.max(0, pct))}%`;
  };

  const activeDecision = useMemo(
    () => (doc ? latestDecision(doc.adaptive.decisions, currentTime) : null),
    [doc, currentTime],
  );

  const visibleEvents: SimEvent[] = useMemo(() => {
    if (!doc) return [];
    const source = eventFilter === "adaptive" ? doc.adaptive.events : doc.fixed.events;
    return source
      .filter((ev) => ev.t <= currentTime && ev.type !== "arrived")
      .slice()
      .reverse();
  }, [doc, eventFilter, currentTime]);

  const jumpToTime = (t: number) => {
    if (!doc) return;
    const idx = Math.max(0, Math.min(maxTick, Math.round(t / doc.scenario.dt_s)));
    setTick(idx);
    setPlaying(false);
  };

  return (
    <div className="space-y-4">
      {/* Scenario & Playback Bar */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <span>Seed</span>
              <select
                aria-label="Scenario seed"
                value={currentSeed}
                onChange={(e) => handleSeedOrBay(Number(e.target.value), currentBay)}
                className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
              >
                {seeds.map((s) => (
                  <option key={s} value={s}>
                    #{s}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <span>Fault Bay</span>
              <select
                aria-label="Fault bay"
                value={currentBay}
                onChange={(e) => handleSeedOrBay(currentSeed, e.target.value)}
                className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
              >
                {["B1", "B2", "B3", "B4", "B5", "B6", "none"].map((b) => (
                  <option key={b} value={b}>
                    {b === "none" ? "None (Control)" : b}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="button"
              onClick={() => setShowGroundTruth((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-300 hover:border-slate-500 hover:text-white"
            >
              {showGroundTruth ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
              Ground truth
            </button>
          </div>

          {/* Playback controls */}
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                setTick(0);
                setPlaying(false);
              }}
              aria-label="Reset playback"
              className="rounded-lg border border-slate-700 bg-slate-950 p-1.5 text-slate-300 hover:border-slate-500 hover:text-white"
            >
              <RotateCcw className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => {
                setPlaying(false);
                setTick((t) => Math.max(0, t - 2));
              }}
              aria-label="Step backward"
              className="rounded-lg border border-slate-700 bg-slate-950 p-1.5 text-slate-300 hover:border-slate-500 hover:text-white"
            >
              <SkipBack className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => {
                if (safeTick >= maxTick) setTick(0);
                setPlaying((p) => !p);
              }}
              aria-label={playing ? "Pause" : "Play"}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400"
            >
              {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
              {playing ? "Pause" : "Play"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPlaying(false);
                setTick((t) => Math.min(maxTick, t + 2));
              }}
              aria-label="Step forward"
              className="rounded-lg border border-slate-700 bg-slate-950 p-1.5 text-slate-300 hover:border-slate-500 hover:text-white"
            >
              <SkipForward className="h-4 w-4" />
            </button>

            <div className="ml-1 flex items-center rounded-lg border border-slate-700 bg-slate-950 p-0.5 font-mono text-xs">
              <FastForward className="mx-1.5 h-3.5 w-3.5 text-slate-500" />
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSpeed(s)}
                  className={`rounded px-2 py-1 ${
                    speed === s ? "bg-slate-800 text-emerald-300" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {s}×
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Timeline Scrubber */}
        {doc && (
          <div className="mt-4">
            <div className="mb-1.5 flex items-center justify-between font-mono text-xs tabular-nums text-slate-400">
              <span>t = {currentTime.toFixed(1)} s</span>
              <span className="text-[11px] text-slate-500">
                {doc.scenario.fault_bay_id
                  ? `Fault onset at ${doc.scenario.fault_onset_s.toFixed(1)} s (${doc.scenario.fault_bay_id})`
                  : "Fault-free control scenario"}
              </span>
              <span>{doc.scenario.horizon_s.toFixed(0)} s</span>
            </div>

            <div className="relative flex items-center">
              <input
                type="range"
                aria-label="Simulation timeline"
                min={0}
                max={maxTick}
                value={safeTick}
                onChange={(e) => {
                  setPlaying(false);
                  setTick(Number(e.target.value));
                }}
                className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-slate-800 accent-emerald-400"
              />
            </div>

            {/* Event markers under scrubber */}
            {doc.scenario.fault_bay_id && (
              <div className="relative mt-1.5 h-4 font-mono text-[10px]">
                <span
                  style={{
                    left: markerLeft(doc.scenario.fault_onset_s),
                  }}
                  className="absolute -translate-x-1/2 text-rose-400"
                  title={`Fault onset at ${doc.scenario.fault_onset_s}s`}
                >
                  ▲ onset
                </span>
                {doc.adaptive.metrics.time_to_confirm_s !== null && (
                  <span
                    style={{
                      left: markerLeft(doc.scenario.fault_onset_s + doc.adaptive.metrics.time_to_confirm_s),
                    }}
                    className="absolute -translate-x-1/2 text-emerald-400"
                  >
                    ● adapt
                  </span>
                )}
                {doc.fixed.metrics.time_to_confirm_s !== null && (
                  <span
                    style={{
                      left: markerLeft(doc.scenario.fault_onset_s + doc.fixed.metrics.time_to_confirm_s),
                    }}
                    className="absolute -translate-x-1/2 text-sky-400"
                  >
                    ● fixed
                  </span>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-xl border border-rose-500/40 bg-rose-950/40 p-4 text-sm text-rose-200">
          {error}
        </div>
      )}

      {loading && !doc && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-10 text-center text-sm text-slate-400">
          Loading paired scenario…
        </div>
      )}

      {doc && (
        <>
          {/* KPI Strip */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <KpiCard
              label="Time Saved"
              value={
                doc.time_saved_s === null
                  ? "Control"
                  : doc.time_saved_s > 0
                    ? `${doc.time_saved_s.toFixed(1)} s faster`
                    : doc.time_saved_s < 0
                      ? `${Math.abs(doc.time_saved_s).toFixed(1)} s slower`
                      : "Tie (0.0 s)"
              }
              tone={
                doc.time_saved_s !== null && doc.time_saved_s > 0
                  ? "emerald"
                  : doc.time_saved_s !== null && doc.time_saved_s < 0
                    ? "amber"
                    : "neutral"
              }
            />
            <KpiCard
              label="Confirm Latency"
              value={
                doc.scenario.fault_bay_id
                  ? `${doc.adaptive.metrics.time_to_confirm_s?.toFixed(1) ?? "—"}s vs ${doc.fixed.metrics.time_to_confirm_s?.toFixed(1) ?? "—"}s`
                  : "No fault"
              }
              sub="Adaptive vs. Fixed"
            />
            <KpiCard
              label="Distance Delta"
              value={`${doc.adaptive.metrics.distance_m - doc.fixed.metrics.distance_m >= 0 ? "+" : ""}${(doc.adaptive.metrics.distance_m - doc.fixed.metrics.distance_m).toFixed(1)} m`}
              sub={`${doc.adaptive.metrics.distance_m.toFixed(1)}m vs ${doc.fixed.metrics.distance_m.toFixed(1)}m`}
            />
            <KpiCard
              label="False Alarms"
              value={`${doc.adaptive.metrics.false_alarms} vs ${doc.fixed.metrics.false_alarms}`}
              sub="0 false confirmations"
            />
            <KpiCard
              label="Max Coverage Gap"
              value={`${doc.adaptive.metrics.max_unseen_s.toFixed(1)} s`}
              sub={`Guardrail ≤ ${doc.scenario.max_unseen_s} s`}
              tone={doc.adaptive.metrics.guardrail_ok ? "emerald" : "amber"}
            />
          </div>

          {/* Side-by-side Floor Maps */}
          <div className="grid gap-4 lg:grid-cols-2">
            <FloorMap
              title="Fixed Patrol (Baseline)"
              subtitle="Clockwise B1→B6 route; ignores line thermocouples"
              accent="sky"
              scenario={doc.scenario}
              run={doc.fixed}
              tick={safeTick}
              showGroundTruth={showGroundTruth}
            />
            <FloorMap
              title="Risk-Adaptive Patrol"
              subtitle="Interrupts route on EWMA line alerts with 90s coverage guardrail"
              accent="emerald"
              scenario={doc.scenario}
              run={doc.adaptive}
              tick={safeTick}
              showGroundTruth={showGroundTruth}
            />
          </div>

          {/* Explainability & Event Trail */}
          <div className="grid gap-4 lg:grid-cols-5">
            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 lg:col-span-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-100">Adaptive Planner Explainability</h3>
                {activeDecision && (
                  <span className="rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 font-mono text-[10px] uppercase text-emerald-300">
                    {activeDecision.mode}
                  </span>
                )}
              </div>

              {activeDecision ? (
                <div className="mt-3 space-y-3">
                  <div className="rounded-lg border border-slate-800 bg-slate-950 p-3">
                    <div className="flex items-center justify-between font-mono text-xs text-slate-400">
                      <span>Selected: {activeDecision.bay}</span>
                      <span>t = {activeDecision.t.toFixed(1)} s</span>
                    </div>
                    <p className="mt-1.5 font-mono text-xs leading-relaxed text-slate-200">
                      {activeDecision.reason}
                    </p>
                  </div>

                  {Object.keys(activeDecision.scores).length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">
                        Alert Candidate Scores
                      </div>
                      {Object.entries(activeDecision.scores)
                        .sort(([, a], [, b]) => b - a)
                        .map(([bay, score]) => (
                          <div key={bay} className="flex items-center gap-2 font-mono text-xs">
                            <span className="w-7 text-slate-300">{bay}</span>
                            <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-800">
                              <div
                                className="h-full rounded-full bg-emerald-400"
                                style={{ width: `${Math.max(4, Math.min(100, score * 100))}%` }}
                              />
                            </div>
                            <span className="w-12 text-right tabular-nums text-slate-300">
                              {score.toFixed(3)}
                            </span>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-xs text-slate-400">Waiting for first planner decision.</p>
              )}

              <div className="mt-4 border-t border-slate-800 pt-3 font-mono text-[11px] text-slate-500">
                score = {doc.config.weight_thermal}·thermal + {doc.config.weight_staleness}
                ·staleness − {doc.config.weight_travel}·travel
              </div>
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 lg:col-span-3">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-100">
                  Event Trail <span className="font-normal text-slate-500">(click to jump)</span>
                </h3>
                <div className="flex rounded-lg border border-slate-700 bg-slate-950 p-0.5 text-xs">
                  {(["adaptive", "fixed"] as const).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      onClick={() => setEventFilter(tab)}
                      className={`rounded px-2.5 py-1 capitalize ${
                        eventFilter === tab
                          ? "bg-slate-800 text-slate-100"
                          : "text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      {tab}
                    </button>
                  ))}
                </div>
              </div>

              <div className="max-h-56 space-y-1.5 overflow-y-auto pr-1">
                {visibleEvents.map((ev, index) => (
                  <button
                    key={`${ev.t}-${ev.type}-${index}`}
                    type="button"
                    onClick={() => jumpToTime(ev.t)}
                    className="flex w-full items-center gap-2.5 rounded-lg border border-slate-800/80 bg-slate-950/80 px-2.5 py-1.5 text-left text-xs hover:border-slate-600"
                  >
                    <span className="w-12 shrink-0 font-mono tabular-nums text-slate-500">
                      {ev.t.toFixed(1)}s
                    </span>
                    <span
                      className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                        EVENT_COLORS[ev.type] ?? "border-slate-700 bg-slate-800/60 text-slate-300"
                      }`}
                    >
                      {ev.type}
                    </span>
                    {ev.bay && (
                      <span className="shrink-0 font-mono text-[11px] font-semibold text-slate-300">
                        {ev.bay}
                      </span>
                    )}
                    <span className="truncate text-slate-300">{ev.message}</span>
                  </button>
                ))}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

function KpiCard({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "neutral" | "emerald" | "amber";
}) {
  const valueClass =
    tone === "emerald" ? "text-emerald-300" : tone === "amber" ? "text-amber-300" : "text-slate-100";
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3">
      <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 font-mono text-base font-semibold tabular-nums ${valueClass}`}>{value}</div>
      {sub && <div className="mt-0.5 font-mono text-[11px] text-slate-400">{sub}</div>}
    </div>
  );
}
