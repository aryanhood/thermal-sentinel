# ADR-003: Deterministic Static Export and Client-Side Replay

**Status:** Accepted

## Context

In v1, a single stateful Python HTTP server held one live experiment in memory. When deployed publicly, one visitor pressing Reset interrupted every other visitor, and free-tier containers suffered ~50 s cold starts.

## Decision

Separate offline computation from interactive presentation:

1. `thermal-sentinel export` deterministically generates compact columnar JSON timelines (`web/public/data/`) and `docs/benchmark.md`.
2. `thermal-sentinel export --check` runs in CI so committed data and documentation can never drift from the simulation code.
3. The React + TypeScript dashboard (`web/`) loads the static JSON and replays timelines smoothly in the browser.

## Consequences

- **Zero shared state:** Every visitor controls their own scenario, scrubber, and playback speed independently.
- **Zero server cost & instant loads:** Deploys cleanly to Vercel or Cloudflare Pages with edge caching and no cold starts.
- **Limitation:** The web UI explores the pre-exported catalog (42 scenarios + 700-case benchmark) rather than arbitrary custom seeds; arbitrary seeds remain available via `thermal-sentinel run --seed N`.
