import { useMemo } from "react";
import type { RunResult, ScenarioSpec } from "../types";

const STATE_LABELS: Record<number, { text: string; color: string }> = {
  0: { text: "IDLE", color: "#94a3b8" },
  1: { text: "TRANSIT", color: "#38bdf8" },
  2: { text: "SCANNING", color: "#a78bfa" },
  3: { text: "CONFIRMING", color: "#fbbf24" },
};

interface FloorMapProps {
  title: string;
  subtitle: string;
  accent: "sky" | "emerald";
  scenario: ScenarioSpec;
  run: RunResult;
  tick: number;
  showGroundTruth: boolean;
}

const PAD_X = 56;
const PAD_Y = 44;
const SCALE = 48; // 1 metre = 48 px
const WIDTH = PAD_X * 2 + 8.5 * SCALE;
const HEIGHT = PAD_Y * 2 + 6 * SCALE;

function toSvg(x: number, y: number): [number, number] {
  return [PAD_X + x * SCALE, PAD_Y + (6 - y) * SCALE];
}

export function FloorMap({ title, subtitle, accent, scenario, run, tick, showGroundTruth }: FloorMapProps) {
  const safeTick = Math.max(0, Math.min(tick, run.frames.t.length - 1));
  const rx = run.frames.x[safeTick] ?? scenario.dock[0];
  const ry = run.frames.y[safeTick] ?? scenario.dock[1];
  const stateCode = run.frames.state[safeTick] ?? 0;
  const targetIndex = run.frames.target[safeTick] ?? -1;
  const bayStatuses = run.frames.status[safeTick] ?? [];
  const bayLines = run.frames.line[safeTick] ?? [];
  const distance = run.frames.distance[safeTick] ?? 0;
  const currentTime = run.frames.t[safeTick] ?? 0;

  const [robotX, robotY] = toSvg(rx, ry);
  const [dockX, dockY] = toSvg(scenario.dock[0], scenario.dock[1]);
  const targetBay = targetIndex >= 0 ? scenario.bays[targetIndex] : undefined;
  const stateInfo = STATE_LABELS[stateCode] ?? { text: "IDLE", color: "#94a3b8" };

  // Build recent path trail (last 70 ticks ~ 35s so the floor stays legible)
  const trailPoints = useMemo(() => {
    const start = Math.max(0, safeTick - 70);
    const pts: string[] = [];
    for (let i = start; i <= safeTick; i += 1) {
      const px = run.frames.x[i];
      const py = run.frames.y[i];
      if (px !== undefined && py !== undefined) {
        const [sx, sy] = toSvg(px, py);
        pts.push(`${sx.toFixed(1)},${sy.toFixed(1)}`);
      }
    }
    return pts.join(" ");
  }, [run.frames.x, run.frames.y, safeTick]);

  const accentStroke = accent === "emerald" ? "#10b981" : "#38bdf8";
  const accentBadge =
    accent === "emerald"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
      : "bg-sky-500/15 text-sky-300 border-sky-500/30";

  const confirmedAt =
    run.metrics.time_to_confirm_s !== null ? scenario.fault_onset_s + run.metrics.time_to_confirm_s : null;
  const isConfirmedNow = confirmedAt !== null && currentTime >= confirmedAt;

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold tracking-tight text-slate-100">{title}</h3>
            <span
              className={`rounded-full border px-2 py-0.5 font-mono text-[10px] font-medium uppercase ${accentBadge}`}
            >
              {stateInfo.text}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>
        </div>

        <div className="flex items-center gap-3 font-mono text-xs tabular-nums">
          <div className="text-right">
            <div className="text-[10px] uppercase text-slate-500">Distance</div>
            <div className="text-slate-200">{distance.toFixed(1)} m</div>
          </div>
          <div className="text-right">
            <div className="text-[10px] uppercase text-slate-500">Status</div>
            <div className={isConfirmedNow ? "text-emerald-400" : "text-slate-300"}>
              {isConfirmedNow
                ? `Confirmed (${run.metrics.time_to_confirm_s?.toFixed(1)}s)`
                : scenario.fault_bay_id
                  ? "Monitoring"
                  : "Control"}
            </div>
          </div>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-800/80 bg-slate-950">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className="h-auto w-full select-none"
          role="img"
          aria-label={`${title} floor plan at ${currentTime.toFixed(1)} seconds`}
        >
          {/* Subtle 1m coordinate grid */}
          {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((metre) => {
            const [gx] = toSvg(metre, 0);
            return (
              <line
                key={`gx-${metre}`}
                x1={gx}
                y1={PAD_Y - 12}
                x2={gx}
                y2={HEIGHT - PAD_Y + 12}
                stroke="#1e293b"
                strokeWidth="1"
                strokeDasharray="2 4"
              />
            );
          })}
          {[0, 1, 2, 3, 4, 5, 6].map((metre) => {
            const [, gy] = toSvg(0, metre);
            return (
              <line
                key={`gy-${metre}`}
                x1={PAD_X - 14}
                y1={gy}
                x2={WIDTH - PAD_X + 14}
                y2={gy}
                stroke="#1e293b"
                strokeWidth="1"
                strokeDasharray="2 4"
              />
            );
          })}

          {/* Nominal clockwise patrol loop */}
          <polygon
            points={scenario.bays
              .map((b) => {
                const [bx, by] = toSvg(b.x, b.y);
                return `${bx},${by}`;
              })
              .join(" ")}
            fill="none"
            stroke="#334155"
            strokeWidth="1.25"
            strokeDasharray="5 5"
          />

          {/* Dock */}
          <g transform={`translate(${dockX}, ${dockY})`}>
            <rect
              x={-16}
              y={-16}
              width={32}
              height={32}
              rx={6}
              fill="#0f172a"
              stroke="#475569"
              strokeWidth="1.5"
            />
            <text x={0} y={3} textAnchor="middle" fill="#94a3b8" fontSize="9" fontFamily="monospace">
              DOCK
            </text>
          </g>

          {/* Robot trail */}
          {trailPoints && (
            <polyline
              points={trailPoints}
              fill="none"
              stroke={accentStroke}
              strokeOpacity="0.45"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {/* Vector to current target */}
          {targetBay && stateCode === 1 && (
            <line
              x1={robotX}
              y1={robotY}
              x2={toSvg(targetBay.x, targetBay.y)[0]}
              y2={toSvg(targetBay.x, targetBay.y)[1]}
              stroke={accentStroke}
              strokeWidth="1.5"
              strokeDasharray="4 4"
              strokeOpacity="0.8"
            />
          )}

          {/* Inspection bays */}
          {scenario.bays.map((bay, idx) => {
            const [bx, by] = toSvg(bay.x, bay.y);
            const status = bayStatuses[idx] ?? 0;
            const delta = bayLines[idx] ?? 0;
            const isTrueFault =
              showGroundTruth && scenario.fault_bay_id === bay.id && currentTime >= scenario.fault_onset_s;

            const fill = status === 2 ? "#450a0a" : status === 1 ? "#451a03" : "#0f172a";
            const stroke = status === 2 ? "#f43f5e" : status === 1 ? "#f59e0b" : "#334155";
            const deltaColor = delta >= 1.2 ? "#fbbf24" : delta >= 0.6 ? "#cbd5e1" : "#64748b";

            return (
              <g key={bay.id} transform={`translate(${bx}, ${by})`}>
                {isTrueFault && (
                  <circle
                    r={31}
                    fill="none"
                    stroke="#f43f5e"
                    strokeWidth="1.5"
                    strokeDasharray="4 3"
                    strokeOpacity="0.85"
                  />
                )}
                {status === 1 && (
                  <circle r={27} fill="none" stroke="#f59e0b" strokeWidth="2" strokeOpacity="0.55" />
                )}
                <rect
                  x={-23}
                  y={-20}
                  width={46}
                  height={40}
                  rx={8}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth="2"
                />
                <text
                  x={0}
                  y={-3}
                  textAnchor="middle"
                  fill="#f8fafc"
                  fontSize="11"
                  fontWeight="600"
                  fontFamily="monospace"
                >
                  {bay.id}
                </text>
                <text
                  x={0}
                  y={12}
                  textAnchor="middle"
                  fill={deltaColor}
                  fontSize="9.5"
                  fontFamily="monospace"
                >
                  {delta >= 0 ? `+${delta.toFixed(1)}°` : `${delta.toFixed(1)}°`}
                </text>
              </g>
            );
          })}

          {/* Robot */}
          <g transform={`translate(${robotX}, ${robotY})`}>
            {(stateCode === 2 || stateCode === 3) && (
              <circle
                r={17}
                fill={stateInfo.color}
                fillOpacity="0.18"
                stroke={stateInfo.color}
                strokeWidth="1.5"
              />
            )}
            <circle r={9.5} fill={accentStroke} stroke="#020617" strokeWidth="2.5" />
            <circle r={3} fill="#f8fafc" />
          </g>
        </svg>
      </div>
    </section>
  );
}
