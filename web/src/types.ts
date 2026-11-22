export interface Bay {
  id: string;
  label: string;
  x: number;
  y: number;
}

export interface ScenarioSpec {
  seed: number;
  fault_bay_id: string | null;
  fault_onset_s: number;
  fault_rise_c_per_min: number;
  fault_max_rise_c: number;
  ambient_c: number;
  max_unseen_s: number;
  horizon_s: number;
  dt_s: number;
  dock: [number, number];
  bays: Bay[];
}

export interface PolicyConfig {
  ewma_alpha: number;
  alert_on_c: number;
  alert_off_c: number;
  rearm_after_s: number;
  watch_c: number;
  confirm_c: number;
  max_dwells: number;
  guardrail_fraction: number;
  weight_thermal: number;
  weight_staleness: number;
  weight_travel: number;
  thermal_scale_c: number;
  travel_scale_m: number;
  cusum_drift_c?: number;
  cusum_threshold_c?: number;
  bayes_alert_prob?: number;
  bayes_clear_prob?: number;
}

export interface RunMetrics {
  fault_present: boolean;
  detected: boolean;
  time_to_confirm_s: number | null;
  distance_m: number;
  scans: number;
  close_inspections: number;
  false_alarms: number;
  false_confirmations: number;
  line_alerts: number;
  retargets: number;
  max_unseen_s: number;
  guardrail_ok: boolean;
}

export interface SimEvent {
  t: number;
  type: string;
  bay: string | null;
  message: string;
}

export interface PlannerDecision {
  t: number;
  bay: string;
  mode: "patrol" | "alert" | "guardrail" | string;
  reason: string;
  scores: Record<string, number>;
}

export interface FrameSeries {
  t: number[];
  x: number[];
  y: number[];
  state: number[]; // 0 IDLE, 1 MOVING, 2 SCANNING, 3 CONFIRMING
  target: number[]; // index into scenario.bays or -1
  status: number[][]; // per bay: 0 NOMINAL, 1 ALERT, 2 CONFIRMED
  line: number[][]; // per bay EWMA delta C
  distance: number[];
}

export interface RunResult {
  policy: "fixed" | "adaptive";
  case_id: string;
  metrics: RunMetrics;
  events: SimEvent[];
  decisions: PlannerDecision[];
  frames: FrameSeries;
}

export interface ScenarioDocument {
  case_id: string;
  scenario: ScenarioSpec;
  config: PolicyConfig;
  time_saved_s: number | null;
  fixed: RunResult;
  adaptive: RunResult;
}

export interface CatalogEntry {
  case_id: string;
  seed: number;
  fault_bay: string | null;
  fault_onset_s: number;
  time_saved_s: number | null;
}

export interface CatalogIndex {
  version: string;
  scenarios: CatalogEntry[];
}

export interface DistributionStats {
  n: number;
  mean: number | null;
  median: number | null;
  p90: number | null;
  min: number | null;
  max: number | null;
}

export interface PolicyBenchmarkSummary {
  detection_rate_pct: number | null;
  time_to_confirm_s: DistributionStats;
  distance_m: DistributionStats;
  false_alarms_per_hour: number | null;
  false_confirmations: number;
  guardrail_ok_pct: number;
  max_unseen_s: DistributionStats;
  mean_time_saved_vs_fixed_s?: number | null;
  time_saved_ci95_s?: [number, number] | null;
}

export interface BenchmarkCase {
  case_id: string;
  seed: number;
  fault_bay: string | null;
  fault_onset_s: number;
  outcome:
    | "adaptive_faster"
    | "adaptive_only"
    | "tie"
    | "fixed_faster"
    | "fixed_only"
    | "neither"
    | "no_fault";
  time_saved_s: number | null;
  fixed: {
    detected: boolean;
    time_to_confirm_s: number | null;
    distance_m: number;
    close_inspections: number;
    false_alarms: number;
    false_confirmations: number;
    max_unseen_s: number;
    guardrail_ok: boolean;
  };
  adaptive: {
    detected: boolean;
    time_to_confirm_s: number | null;
    distance_m: number;
    close_inspections: number;
    false_alarms: number;
    false_confirmations: number;
    max_unseen_s: number;
    guardrail_ok: boolean;
  };
}

export interface SensitivityCellPolicyMetrics {
  detection_rate_pct: number | null;
  ttc_median_s: number | null;
  ttc_mean_s: number | null;
  ttc_p90_s: number | null;
  false_alarms_per_hour: number | null;
  false_confirmations: number;
  distance_mean_m: number | null;
  guardrail_ok_pct: number;
}

export interface SensitivityCell {
  line_glitch_prob: number;
  fault_rise_c_per_min: number;
  fault_cases: number;
  simulated_hours: number;
  policies: Record<string, SensitivityCellPolicyMetrics>;
}

export interface BenchmarkReport {
  schema_version: string;
  method: {
    seeds: number[];
    fault_choices: string[];
    fault_onset: string;
    horizon_s: number | null;
    pairing: string;
  };
  summary: {
    cases: number;
    fault_cases: number;
    no_fault_cases: number;
    simulated_hours: number;
    fixed: PolicyBenchmarkSummary;
    adaptive: PolicyBenchmarkSummary;
    policies?: Record<string, PolicyBenchmarkSummary>;
    paired: {
      outcomes: Record<string, number>;
      time_saved_s: DistributionStats;
      time_saved_ci95_s: [number, number] | null;
      distance_overhead_pct: DistributionStats;
    };
  };
  sensitivity_sweep?: SensitivityCell[];
  cases: BenchmarkCase[];
}
