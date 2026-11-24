# Thermal Sentinel

An industrial thermal monitoring prototype built with **FastAPI**, **SQLite**, and a paired robot inspection simulation.

It receives temperature readings over a REST API, stores readings and equipment state in SQLite, detects abnormal heating trends while filtering brief sensor glitches, and decides which equipment bay an inspection robot should check next.

> **Note:** This is a software monitoring prototype, not a production factory system. The backend API, database, anomaly detectors, and inspection planner are working software, while the test scenarios come from a deterministic simulation rather than physical sensors or real robot hardware.

---

## What It Does

- **Sensor Ingestion:** Accepts single or batch temperature readings through validated FastAPI endpoints. Late out-of-order readings are archived without changing live detector state.
- **SQLite Storage:** Saves equipment bays, timestamped readings, alerts, and inspection logs using serialized SQLite transactions.
- **Trend Monitoring:** Tracks heating above each bay's baseline using EWMA, Page's CUSUM, and a 3-state Bayesian HMM, opening and resolving alerts automatically.
- **Inspection Planner:** Ranks which bay needs attention next based on fault risk, time since last visit, and travel distance, and logs close-range verification checks.
- **Simulation Testbed:** Uses a 6-bay simulation to benchmark policies on identical noise streams and replay fault scenarios into the API (non-destructive by default).

```mermaid
flowchart LR
    S["Sensor / Simulator"] --> API["FastAPI REST API"]
    API --> MON["Monitoring Engine (EWMA · CUSUM · HMM)"]
    MON <--> DB[("SQLite")]
    API --> PLAN["Inspection Planner"]
    DB <--> PLAN
```

---

## Main API Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Health check, database status, and open alert count |
| `GET / POST /api/v1/equipment` | List or register equipment bays |
| `PATCH /api/v1/equipment/{bay_id}/baseline` | Update baseline temperature and close active alerts |
| `POST / GET /api/v1/readings` | Ingest or query timestamped readings (`/batch` for batches) |
| `GET / POST /api/v1/alerts` | List alerts or acknowledge via `/{alert_id}/acknowledge` |
| `GET /api/v1/inspection/next` | Get the next recommended bay for robot inspection |
| `POST /api/v1/inspection/verify` | Record a close-range camera check and update alert status |
| `POST /api/v1/simulation/replay` | Replay a simulated fault scenario into the API |

Full interactive docs are available at `http://127.0.0.1:8000/docs` when running locally.

---

## Quick Start

### Run Locally

```bash
uv sync
uv run thermal-sentinel serve --host 127.0.0.1 --port 8000
```

Try an end-to-end replay in another terminal:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/simulation/replay \
  -H "Content-Type: application/json" \
  -d '{"seed": 3, "fault_bay_id": "B5", "max_ticks": 140}'

curl http://127.0.0.1:8000/api/v1/inspection/next
```

### Run with Docker

```bash
docker compose up --build
```

### Run Tests and Simulation

```bash
uv run pytest --cov
uv run ruff check . && uv run mypy
uv run thermal-sentinel benchmark
cd web && npm install && npm run dev   # static replay dashboard
```

---

## Simulation Benchmark

Five policies evaluated across **700 paired scenarios** (`35.0` simulated hours per policy) on identical sensor noise and glitches. Full breakdown: [docs/benchmark.md](docs/benchmark.md).

| Policy | Detect % | Median Confirm Time | Saved vs. Fixed | False Alarms / hr | Guardrail % |
|---|---:|---:|---:|---:|---:|
| Fixed Patrol | 100.0% | 33.0 s | 0.0 s | 0.0 | 100.0% |
| Greedy EWMA | 100.0% | 18.5 s | 14.28 s | 6.69 | 100.0% |
| Risk-Adaptive EWMA | 100.0% | 18.5 s | 14.3 s | 6.57 | 100.0% |
| Page CUSUM | 100.0% | 22.5 s | 11.15 s | 0.57 | 100.0% |
| **3-State Bayesian HMM** | **100.0%** | **16.0 s** | **17.01 s** | **0.09** | **100.0%** |

![Latency vs. False Alarms Pareto Frontier](assets/pareto_sensitivity.png)

---

## Limitations

- **Prototype only:** No direct integration with physical PLCs, MQTT brokers, or real robot hardware.
- **Simplified physics:** The simulation uses linear heating ramps and constant-speed straight-line robot travel without obstacles.
- **Detector prior:** The Bayesian HMM assumes transient spikes are around `+3.0 °C`; Page's CUSUM (`THERMAL_SENTINEL_POLICY=cusum`) is available as a simpler alternative when spike size is unknown.

---

## Docs

[Architecture](docs/architecture.md) · [Benchmark Report](docs/benchmark.md) · [ADRs](docs/adr/0001-paired-shared-environment.md) · MIT License
