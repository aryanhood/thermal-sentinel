# ADR-001: Paired Evaluation on a Shared, Pre-Sampled Environment

**Status:** Accepted

## Context

Comparing two patrol policies across separate random runs conflates policy differences with random draw luck (different fault onset times, sensor noise, or glitch timing).

## Decision

Construct an immutable `Environment(scenario)` once per case before either policy runs. Line-sensor readings (including noise and transient glitches) are pre-sampled for every `(bay, tick)` using SHA-256-derived independent RNG streams (`seeded_rng`). Camera readings are keyed by `(seed, "camera", bay_id, tick, close)`.

## Consequences

- Any difference in detection latency, distance, or false alarms is strictly attributable to the policy decision rule.
- Adding a new RNG stream never shifts existing streams, keeping published benchmark numbers reproducible across versions.
- Memory footprint per scenario is tiny (~6 bays × 361 ticks ≈ 2,166 floats).
