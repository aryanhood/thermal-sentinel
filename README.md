# Thermal Sentinel

> **The robot doesn't follow a route. It follows risk.**

A simulation engine, digital twin for a robot patrol system. The goal is to simulate an inspection process and compute how quickly a real fault is confirmed.comparing two patrol strategies:

fixed: keeps a steady route
adaptive: interrupts patrol when heat/rise signals suggest a problem

```bash
python run.py
```

1. Both policies begin with the same six-bay scenario.
2. A weak thermal ramp emerges at Bay 05.
3. The adaptive planner exposes the risk score behind its route change.
4. It takes an immediate, lower-noise close inspection before confirming.
5. The fixed patrol completes its cycle before performing the same check.
6. The dashboard shows confirmation lead, route distance, coverage, and the
   ordered event trail; the completed result is retained in SQLite.

Python 3.11+ is the only local prerequisite. There are no third-party runtime
or test dependencies.

### Docker

```bash
docker compose up --build
```

Then open <http://127.0.0.1:8080>. The container uses a named volume for
completed experiment evidence.

## Run verification

From PowerShell:

```powershell
python -c "import sys,unittest; sys.path.insert(0, 'src'); suite=unittest.defaultTestLoader.discover('tests'); result=unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(not result.wasSuccessful())"
python -c "import sys,runpy; sys.path.insert(0, 'src'); runpy.run_module('thermal_sentinel.benchmark', run_name='__main__')"
```

On macOS/Linux, editable install is also available:

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
thermal-sentinel-benchmark --compact
```

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness and version |
| `GET` | `/api/experiment` | Current paired-run snapshot |
| `POST` | `/api/experiment/start` | Start/resume the live experiment |
| `POST` | `/api/experiment/pause` | Pause automatic stepping |
| `POST` | `/api/experiment/step` | Advance one decision while paused |
| `POST` | `/api/experiment/reset` | Reset, optionally with `{"seed": 42}` |
| `GET` | `/api/runs` | List retained completed evidence |
| `GET` | `/api/runs/{id}` | Retrieve a complete run document |

## Engineering notes

- [System design](docs/system-design.md)
- [Architecture decisions](docs/decisions.md)
- [Security posture](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

MIT license
