# Contributing

Changes should stay focused on the core question: comparing inspection policies fairly and reproducibly.

## Local Setup

```bash
uv sync
cd web && npm ci
```

## Verification Before Opening a PR

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest --cov
uv run thermal-sentinel export --check
cd web && npm run check
```

If you change simulation parameters or policy logic, regenerate the static data and benchmark table:

```bash
uv run thermal-sentinel export
```

Include the motivation for the change and verify that determinism and paired-environment fairness are preserved.
