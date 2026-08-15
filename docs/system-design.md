## Simulation-to-reality gaps

- Thermal telemetry is a compact signal model, not a battery electrochemistry
  or heat-transfer model.
- Travel time assumes free space and perfect localization.
- Close inspection has simplified measurement noise and no sensor calibration.
- A real deployment would need independent safety assessment, cyber security,
  degraded-mode handling, and validation against recorded physical tests.

core:

- `inspection_core` — dependency-free scenario, fixed/adaptive policies,
  close-confirmation state machine, events, and KPIs.
- `thermal_sentinel.service` — thread-safe paired-run lifecycle.
- `thermal_sentinel.api` — standard-library HTTP and static-file server.
- `thermal_sentinel.store` — completed-evidence adapter using SQLite.
- `thermal_sentinel.benchmark` — deterministic 24-scenario evidence matrix.
- `frontend` — responsive operations dashboard with an SVG floor map.