# Thermal Sentinel — Sequential Change-Point Detection & Risk-Adaptive Dispatch

**A reproducible discrete-event benchmark evaluating fixed patrols against EWMA, Page CUSUM, and 3-state Bayesian HMM sequential anomaly detectors under shared stochastic environments.**

---

## 1. Research Question

In an automated six-bay test cell, fixed thermocouples stream noisy temperature deltas while transient sensor glitches inject `3.0 s` false-alarm spikes. When a thermocouple signals an anomaly, should a mobile inspection robot stay on a deterministic patrol route, dispatch greedily using single-threshold EWMA, integrate residuals with Page's CUSUM, or filter transient spikes with a 3-state Bayesian Hidden Markov Model?

Thermal Sentinel pre-computes every baseline temperature, noise realization, glitch burst, and camera reading per SHA-256 stream (`seeded_rng`) before any policy executes, guaranteeing **identical shared environments** across all five policies.

---

## 2. Five-Policy Benchmark (700 Paired Scenarios)

Evaluated across `100` SHA-256 seeds `x` `7` fault configurations (`B1..B6` fault ramp at `+12 °C/min`, plus `1` fault-free control), totaling `700` paired scenarios (`35.0` simulated hours per policy). Full report: [`docs/benchmark.md`](docs/benchmark.md).

| Policy | Detect % | TTC Median | TTC p90 | Mean Saved vs Fixed | Mean Dist | False Alarms/hr | Worst Gap | Guardrail % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Fixed Patrol | 100.0% | 33.0 s | 50.5 s | 0.0 s | 79.4 m | 0.0 | 49.5 s | 100.0% |
| Greedy EWMA | 100.0% | 18.5 s | 22.5 s | 14.28 s | 82.84 m | 6.69 | 75.5 s | 100.0% |
| Risk-Adaptive EWMA | 100.0% | 18.5 s | 22.5 s | 14.3 s | 82.87 m | 6.57 | 75.5 s | 100.0% |
| Page CUSUM | 100.0% | 22.5 s | 26.0 s | 11.15 s | 82.14 m | 0.57 | 74.0 s | 100.0% |
| **3-State Bayesian HMM** | **100.0%** | **16.0 s** | **19.5 s** | **17.01 s** | 82.52 m | **0.09** | **66.5 s** | **100.0%** |

- **Paired EWMA vs. Fixed outcomes (`600` fault cases):** Risk-Adaptive EWMA confirms faster in `502` runs (`83.7%`), ties in `77`, and is slower in `21`, with mean time saved `14.3 s` (`95% bootstrap CI: 13.35 – 15.27 s`) and zero false confirmations.
- **Sequential Filtering Gains:** Integrating raw residuals with Page's one-sided CUSUM cuts false-alarm detours by `11.5x` (`6.57 -> 0.57 / hr`), while the exact 3-state Bayesian HMM (`{NOMINAL, GLITCH, FAULT_RAMP}`) cuts baseline false alarms by `73x` (`0.09 / hr`) and achieves the fastest median confirmation (`16.0 s`).

---

## 3. 2D Sensitivity Sweep (`Glitch Rate × Ramp Rate`)

![Latency vs. False Alarms Pareto Frontier and Ramp-Rate Sensitivity](assets/pareto_sensitivity.png)

Across `12` operating regimes (`line_glitch_prob in {0.0, 0.0005, 0.002, 0.005}` × `fault_rise_c_per_min in {6, 12, 24} °C/min`, `90` fault cases per cell):
- **Greedy vs. Risk-Adaptive EWMA:** Removing the coverage guardrail and linear risk score (`Greedy EWMA`) yields nearly identical latency (`18.5 s` median) at baseline glitch rates, but under heavy clutter (`p_glitch = 0.005`), **both** EWMA policies experience `52.9–61.6` false alarms/hr and drop to `85.6%–93.3%` coverage SLA compliance as frequent alerts continuously preempt idle guardrail checks.
- **CUSUM Robustness vs. Slow Ramps:** Page's CUSUM holds false alarms at `0.0–1.11 / hr` up to `4x` nominal glitch density (`p = 0.002`) without knowing glitch amplitude, at the cost of `+4.0–5.5 s` median latency on slow `6 °C/min` faults.

---

## 4. Policies & Architecture

- **`FIXED`:** Clockwise `B1 -> B6` patrol; ignores line telemetry.
- **`GREEDY_EWMA`:** EWMA hysteresis (`alert_on_c = 1.2 °C`, `alert_off_c = 0.6 °C`); dispatches to nearest alerted bay with no coverage guardrail.
- **`ADAPTIVE`:** EWMA hysteresis + bounded linear score `0.7·thermal + 0.2·staleness - 0.1·travel` + `80%` staleness guardrail ([ADR-002](docs/adr/0002-explainable-scoring.md)).
- **`CUSUM`:** Page's one-sided cumulative sum `S_i(t) = max(0, S_i(t-1) + (delta_i(t) - 0.6))` resetting upon negative close inspection.
- **`BAYESIAN`:** Exact pure-stdlib 3-state HMM forward filter over `{NOMINAL ~ N(0, 0.35), GLITCH ~ N(3.0, 0.35), FAULT_RAMP ~ N(mu_f, 0.65)}` dispatching by expected risk-reduction rate `P(fault) * delta / (travel_time + inspection_time) + 0.15 * staleness`.

---

## 5. Limitations & Negative Results

1. **Matched Prior Advantage in the Bayesian HMM:** The 3-state HMM achieves `0.0` false alarms/hr in the sweep partly because its transient glitch state emission prior `N(3.0, 0.35)` matches the simulator's `line_glitch_c = 3.0 °C` transient spike distribution. Under unmodeled sustained calibration offsets or different spike magnitudes, CUSUM degrades more gracefully because it assumes only drift and noise scale.
2. **Guardrail Preemption Under Heavy Clutter (`p = 0.005`):** The `0.8 * max_unseen_s` staleness guardrail only runs when no bay holds an active alert; under `10x` glitch clutter, continuous EWMA alerts preempt the guardrail, dropping 90 s SLA compliance to `85.6%`. Hard preemption overrides are required if strict SLA guarantees must hold under adversarial noise.
3. **Synthetic Physical Abstractions:** Robot motion uses obstacle-free Euclidean travel at constant speed `1.0 m/s` and independent bay thermal ramps without cross-bay conductive heat diffusion or battery charging constraints.

---

## 6. Quick Start & Verification

### Python CLI (`thermal-sentinel`)

```bash
pip install -e .

# Run a single paired scenario
thermal-sentinel run --seed 3 --fault B5

# Run the full 700-case five-policy benchmark
thermal-sentinel benchmark

# Regenerate static JSON + docs/benchmark.md (zero drift verified in CI)
thermal-sentinel export --check

# Regenerate Figure assets/pareto_sensitivity.png
python scripts/plot_pareto.py
```

### Interactive Web Dashboard (`web/`)

```bash
cd web
npm install
npm run check   # tsc --noEmit + biome + vitest + vite build
npm run dev
```

### Quality Gates

| Gate | Command | Status / Threshold |
|---|---|---|
| Python lint | `ruff check .` | Zero warnings |
| Strict typing | `mypy` | `strict = true`, zero errors |
| Tests & Branch Coverage | `pytest --cov` | Unit & Hypothesis tests (`>= 90%` branch coverage) |
| Export Consistency | `thermal-sentinel export --check` | Zero drift across JSON & Markdown |
| Web Studio | `npm run check` | `tsc`, `biome`, `vitest`, production `vite build` |

---

## Documentation

- [System Architecture & Simulation-to-Reality Gaps](docs/architecture.md)
- [Generated Five-Policy & Sensitivity Benchmark](docs/benchmark.md)
- [ADR-001: Paired Evaluation on a Shared Environment](docs/adr/0001-paired-shared-environment.md)
- [ADR-002: Explainable Risk Scoring](docs/adr/0002-explainable-scoring.md)
- [ADR-003: Deterministic Static Export & Client-Side Replay](docs/adr/0003-static-export-and-client-replay.md)

MIT License
