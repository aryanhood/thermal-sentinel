.PHONY: setup test lint export web serve check

setup:
	uv sync
	cd web && npm install

test:
	uv run pytest --cov
	cd web && npm test

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	cd web && npm run typecheck && npm run lint

export:
	uv run thermal-sentinel export

web:
	cd web && npm run dev

serve:
	uv run thermal-sentinel serve

check: lint test
	uv run thermal-sentinel export --check
	cd web && npm run build
