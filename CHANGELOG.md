# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-11-21

### Added
- Time-stepped paired simulation engine (`thermal_sentinel.model`, `environment`, `simulation`) where both policies share an immutable, pre-sampled `Environment` (identical baselines, thermocouple noise, transient glitches, and camera noise).
- 700-scenario paired benchmark (`thermal-sentinel benchmark`) covering 100 seeds × 7 fault configurations (6 fault bays + 1 fault-free control) with 95% percentile bootstrap confidence intervals.
- Deterministic static JSON & Markdown exporter (`thermal-sentinel export` / `--check`) for zero-backend, per-visitor isolated web deployment.
- React + TypeScript + Vite interactive dashboard (`web/`) with synchronized side-by-side SVG floor-plan replay, risk-score explainability, and 700-case benchmark analytics.
- Property-based tests (Hypothesis), strict `mypy`, `ruff`, branch coverage enforcement (≥ 90%), and GitHub Actions CI.

### Changed
- Replaced the single-process stateful HTTP server and shared global experiment with a stateless static-export architecture suitable for Vercel / Cloudflare Pages.
