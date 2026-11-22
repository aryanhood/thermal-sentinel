import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { BenchmarkReport, CatalogIndex, ScenarioDocument } from "./types";

const MOCK_CATALOG: CatalogIndex = {
  version: "0.1.0",
  scenarios: [
    { case_id: "seed-3-b5", seed: 3, fault_bay: "B5", fault_onset_s: 34.5, time_saved_s: 34.0 },
    { case_id: "seed-3-none", seed: 3, fault_bay: null, fault_onset_s: 34.5, time_saved_s: null },
  ],
};

const MOCK_DOC: ScenarioDocument = {
  case_id: "seed-3-b5",
  scenario: {
    seed: 3,
    fault_bay_id: "B5",
    fault_onset_s: 34.5,
    fault_rise_c_per_min: 12.0,
    fault_max_rise_c: 15.0,
    ambient_c: 28.0,
    max_unseen_s: 90.0,
    horizon_s: 180.0,
    dt_s: 0.5,
    dock: [0, 3],
    bays: [
      { id: "B1", label: "Bay 01", x: 2, y: 1 },
      { id: "B2", label: "Bay 02", x: 5, y: 1 },
      { id: "B3", label: "Bay 03", x: 8, y: 1 },
      { id: "B4", label: "Bay 04", x: 8, y: 5 },
      { id: "B5", label: "Bay 05", x: 5, y: 5 },
      { id: "B6", label: "Bay 06", x: 2, y: 5 },
    ],
  },
  config: {
    ewma_alpha: 0.3,
    alert_on_c: 1.2,
    alert_off_c: 0.6,
    rearm_after_s: 15.0,
    watch_c: 1.0,
    confirm_c: 1.5,
    max_dwells: 2,
    guardrail_fraction: 0.8,
    weight_thermal: 0.7,
    weight_staleness: 0.2,
    weight_travel: 0.1,
    thermal_scale_c: 3.0,
    travel_scale_m: 10.0,
  },
  time_saved_s: 34.0,
  fixed: {
    policy: "fixed",
    case_id: "seed-3-b5",
    metrics: {
      fault_present: true,
      detected: true,
      time_to_confirm_s: 51.5,
      distance_m: 78.83,
      scans: 20,
      close_inspections: 1,
      false_alarms: 0,
      false_confirmations: 0,
      line_alerts: 1,
      retargets: 0,
      max_unseen_s: 49.5,
      guardrail_ok: true,
    },
    events: [{ t: 0, type: "mission_started", bay: null, message: "Fixed patrol started from dock" }],
    decisions: [{ t: 0, bay: "B1", mode: "patrol", reason: "Patrol route: next stop Bay 01", scores: {} }],
    frames: {
      t: [0, 0.5],
      x: [0, 0.5],
      y: [3, 2.5],
      state: [0, 1],
      target: [-1, 0],
      status: [
        [0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0],
      ],
      line: [
        [0, 0, 0, 0, 0, 0],
        [0.1, 0, 0, 0, 0, 0],
      ],
      distance: [0, 0.5],
    },
  },
  adaptive: {
    policy: "adaptive",
    case_id: "seed-3-b5",
    metrics: {
      fault_present: true,
      detected: true,
      time_to_confirm_s: 17.5,
      distance_m: 83.83,
      scans: 18,
      close_inspections: 2,
      false_alarms: 1,
      false_confirmations: 0,
      line_alerts: 2,
      retargets: 1,
      max_unseen_s: 56.5,
      guardrail_ok: true,
    },
    events: [
      { t: 0, type: "mission_started", bay: null, message: "Adaptive patrol started from dock" },
      { t: 0.5, type: "retarget", bay: "B5", message: "Route interrupted: Bay 01 → Bay 05" },
    ],
    decisions: [
      {
        t: 0,
        bay: "B5",
        mode: "alert",
        reason: "Risk 0.65 = 0.7·thermal 0.80 + 0.2·staleness 0.50 − 0.1·travel 0.10",
        scores: { B5: 0.65 },
      },
    ],
    frames: {
      t: [0, 0.5],
      x: [0, 0.5],
      y: [3, 3.5],
      state: [0, 1],
      target: [-1, 4],
      status: [
        [0, 0, 0, 0, 1, 0],
        [0, 0, 0, 0, 1, 0],
      ],
      line: [
        [0, 0, 0, 0, 1.4, 0],
        [0, 0, 0, 0, 1.5, 0],
      ],
      distance: [0, 0.5],
    },
  },
};

const MOCK_BENCHMARK: BenchmarkReport = {
  schema_version: "2.0",
  method: {
    seeds: [1, 100],
    fault_choices: ["B1", "B2", "B3", "B4", "B5", "B6", "none"],
    fault_onset: "uniform 10-70 s",
    horizon_s: 180,
    pairing: "shared environment",
  },
  summary: {
    cases: 700,
    fault_cases: 600,
    no_fault_cases: 100,
    simulated_hours: 35,
    fixed: {
      detection_rate_pct: 100,
      time_to_confirm_s: { n: 600, mean: 32.86, median: 33.0, p90: 50.5, min: 9, max: 57 },
      distance_m: { n: 700, mean: 79.4, median: 78.83, p90: 82.83, min: 78.83, max: 82.83 },
      false_alarms_per_hour: 0,
      false_confirmations: 0,
      guardrail_ok_pct: 100,
      max_unseen_s: { n: 700, mean: 48.64, median: 49.5, p90: 49.5, min: 43.5, max: 49.5 },
    },
    adaptive: {
      detection_rate_pct: 100,
      time_to_confirm_s: { n: 600, mean: 18.56, median: 18.5, p90: 22.5, min: 9, max: 32.5 },
      distance_m: { n: 700, mean: 82.87, median: 82.83, p90: 85.57, min: 75.83, max: 91.45 },
      false_alarms_per_hour: 6.57,
      false_confirmations: 0,
      guardrail_ok_pct: 100,
      max_unseen_s: { n: 700, mean: 55.01, median: 55.5, p90: 64.5, min: 43.5, max: 75.5 },
    },
    paired: {
      outcomes: { adaptive_faster: 502, tie: 77, fixed_faster: 21 },
      time_saved_s: { n: 600, mean: 14.3, median: 12.5, p90: 33.0, min: -8, max: 43.5 },
      time_saved_ci95_s: [13.35, 15.27],
      distance_overhead_pct: { n: 700, mean: 4.39, median: 3.81, p90: 8.45, min: -4.83, max: 16.01 },
    },
  },
  cases: [
    {
      case_id: "seed-3-b5",
      seed: 3,
      fault_bay: "B5",
      fault_onset_s: 34.5,
      outcome: "adaptive_faster",
      time_saved_s: 34.0,
      fixed: {
        detected: true,
        time_to_confirm_s: 51.5,
        distance_m: 78.83,
        close_inspections: 1,
        false_alarms: 0,
        false_confirmations: 0,
        max_unseen_s: 49.5,
        guardrail_ok: true,
      },
      adaptive: {
        detected: true,
        time_to_confirm_s: 17.5,
        distance_m: 83.83,
        close_inspections: 2,
        false_alarms: 1,
        false_confirmations: 0,
        max_unseen_s: 56.5,
        guardrail_ok: true,
      },
    },
  ],
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const payload = url.endsWith("index.json")
        ? MOCK_CATALOG
        : url.endsWith("benchmark.json")
          ? MOCK_BENCHMARK
          : MOCK_DOC;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(payload),
      });
    }),
  );
});

describe("Thermal Sentinel Dashboard", () => {
  it("renders side-by-side floor maps and explainability for a paired scenario", async () => {
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText("Fixed Patrol (Baseline)")).toBeInTheDocument();
      expect(screen.getByText("Risk-Adaptive Patrol")).toBeInTheDocument();
    });
    expect(screen.getByText("34.0 s faster")).toBeInTheDocument();
    expect(screen.getByText(/Risk 0.65 = 0.7·thermal/)).toBeInTheDocument();
  });

  it("switches to the 700-case benchmark tab and allows jumping back to replay", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /700-Case Benchmark/i }));
    await waitFor(() => {
      expect(screen.getByText("14.3 s")).toBeInTheDocument();
      expect(screen.getByText(/95% bootstrap CI: 13.35 – 15.27 s/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Open/i }));
    await waitFor(() => {
      expect(screen.getByText("Fixed Patrol (Baseline)")).toBeInTheDocument();
    });
  });
});
