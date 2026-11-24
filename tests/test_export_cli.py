from __future__ import annotations

import json
from pathlib import Path

import pytest
import uvicorn

from thermal_sentinel import benchmark as benchmark_module
from thermal_sentinel import export as export_module
from thermal_sentinel.cli import main
from thermal_sentinel.export import BENCHMARK_DOC, DATA_DIR, check_export, export


@pytest.fixture(autouse=True)
def small_benchmark(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep export tests fast: the real export uses 100 seeds."""
    real = benchmark_module.run_benchmark
    monkeypatch.setattr(export_module, "run_benchmark", lambda config=None: real(range(1, 4), config, sweep_seeds=[1]))
    monkeypatch.setattr(export_module, "DEMO_SEEDS", (1, 2))


def test_export_then_check_is_clean(tmp_path: Path) -> None:
    written = export(tmp_path)
    assert len(written) == 2 * 7 + 3
    assert check_export(tmp_path) == []
    index = json.loads((tmp_path / DATA_DIR / "index.json").read_text(encoding="utf-8"))
    assert len(index["scenarios"]) == 14
    assert (tmp_path / BENCHMARK_DOC).read_text(encoding="utf-8").startswith("# Benchmark results")


def test_check_detects_stale_missing_and_unexpected(tmp_path: Path) -> None:
    export(tmp_path)
    (tmp_path / DATA_DIR / "index.json").write_text("{}", encoding="utf-8")
    (tmp_path / BENCHMARK_DOC).unlink()
    (tmp_path / DATA_DIR / "scenarios" / "seed-99-b1.json").write_text("{}", encoding="utf-8")
    problems = check_export(tmp_path)
    assert any(p.endswith("index.json") for p in problems)
    assert any(p.endswith("benchmark.md") for p in problems)
    assert any("unexpected" in p for p in problems)


def test_export_removes_stale_scenarios(tmp_path: Path) -> None:
    stale = tmp_path / DATA_DIR / "scenarios" / "seed-99-b1.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    export(tmp_path)
    assert not stale.exists()


def test_exported_json_is_byte_stable(tmp_path: Path) -> None:
    export(tmp_path / "a")
    export(tmp_path / "b")
    for path in (tmp_path / "a").rglob("*.*"):
        assert path.read_bytes() == (tmp_path / "b" / path.relative_to(tmp_path / "a")).read_bytes()
        assert b"\r\n" not in path.read_bytes()


def test_cli_export_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export", "--root", str(tmp_path), "--check"]) == 1
    assert main(["export", "--root", str(tmp_path)]) == 0
    assert main(["export", "--root", str(tmp_path), "--check"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_cli_run_text_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--seed", "3", "--fault", "B5"]) == 0
    assert "time to confirm" in capsys.readouterr().out
    assert main(["run", "--seed", "3", "--fault", "none", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["time_saved_s"] is None


def test_cli_benchmark(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["benchmark", "--seeds", "2"]) == 0
    assert "paired cases" in capsys.readouterr().out
    assert main(["benchmark", "--seeds", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cases"] == 14
    assert main(["benchmark", "--seeds", "1", "--json", "--cases"]) == 0
    assert len(json.loads(capsys.readouterr().out)["cases"]) == 7
    assert main(["benchmark", "--seeds", "0"]) == 2


def test_cli_rejects_unknown_bay() -> None:
    with pytest.raises(SystemExit):
        main(["run", "--fault", "B9"])


def test_cli_serve_invokes_uvicorn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_run(app: object, *, host: str, port: int) -> None:
        called["app"] = app
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert main(["serve", "--port", "0"]) == 2
    assert main(["serve", "--port", "70000"]) == 2
    rc = main(["serve", "--host", "127.0.0.1", "--port", "8123", "--db", str(tmp_path / "srv.db"), "--policy", "cusum"])
    assert rc == 0
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8123
