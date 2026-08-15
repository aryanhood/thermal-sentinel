# Engineering decisions

## ADR-001: Compare policies, do not simulate a factory

**Status:** accepted

The useful R&D question is whether risk-adaptive targeting confirms an emerging
thermal anomaly sooner under explicit coverage and distance guardrails. A rich
3D world would add demo surface but no evidence for that question. The project
therefore uses a deterministic 2D discrete-event simulation.

## ADR-002: Explainable scoring, not machine learning

**Status:** accepted

There is no training dataset in this project. Calling the score “AI” would be
misleading. A decomposable policy is easier to inspect, tune, reproduce, and
eventually compare with learned policies if real evidence justifies them.

## ADR-003: One process and the Python standard library

**Status:** accepted

The simulator, HTTP API, static asset server, and local SQLite adapter fit in
one process. This makes the demo portable and removes package-install and
service-orchestration failure modes. Module boundaries preserve the option to
split components later without paying that operational cost now.

## ADR-004: Paired seeded experiments

**Status:** accepted

A fast adaptive run is meaningless if its fault or noise differs from the
baseline. Each comparison runs both policies against the same immutable
scenario parameters and stores a reproducibility digest.

## ADR-005: Persist only completed evidence

**Status:** accepted

Live state is transient. Completed run summaries and their event trails are
written to SQLite as one document. This avoids partially committed experiments
and keeps schema evolution modest for a demonstrator.

