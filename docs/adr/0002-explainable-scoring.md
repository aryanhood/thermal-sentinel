# ADR-002: Explainable Risk Scoring Over Black-Box ML

**Status:** Accepted

## Context

Adaptive routing could be framed as a reinforcement-learning problem, but there is no physical plant dataset in this repository and an opaque policy is difficult to audit in safety-critical inspection environments.

## Decision

Use a transparent, bounded linear risk function:

```text
score = 0.7 * clip(ewma / 3.0) + 0.2 * clip(staleness / max_unseen_s) - 0.1 * clip(distance / 10.0)
```

combined with explicit hysteresis (`alert_on_c` / `alert_off_c`) and a hard coverage guardrail (`0.8 * max_unseen_s`). Every planner decision logs its exact term breakdown.

## Consequences

- Operators and engineers can inspect why every retarget occurred.
- Trade-offs (e.g., false-alarm detours vs. confirmation speed) are tunable via `PolicyConfig` without retraining.
- Provides a strong, reproducible baseline if learned policies are ever evaluated against real plant data.
