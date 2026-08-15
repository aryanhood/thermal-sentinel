"""Zero-dependency HTTP API and static dashboard server."""

from __future__ import annotations

import json
import mimetypes
import os
import signal
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .service import ExperimentService
from .store import ExperimentStore

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


class SentinelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: ExperimentService) -> None:
        super().__init__(address, RequestHandler)
        self.service = service


class RequestHandler(BaseHTTPRequestHandler):
    server: SentinelServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"[http] {self.address_string()} {format % args}\n")

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": status.phrase, "message": message})

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 16_384:
            raise ValueError("request body is too large")
        if not length:
            return {}
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            raise ValueError("request body must be valid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/health":
                self._json(HTTPStatus.OK, {"status": "ok", "service": "thermal-sentinel", "version": "0.1.0"})
            elif path == "/api/experiment":
                self._json(HTTPStatus.OK, self.server.service.snapshot())
            elif path == "/api/runs":
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["20"])[0])
                self._json(HTTPStatus.OK, {"runs": self.server.service.store.list(limit)})
            elif path.startswith("/api/runs/"):
                run = self.server.service.store.get(path.removeprefix("/api/runs/"))
                if run is None:
                    self._error(HTTPStatus.NOT_FOUND, "run not found")
                else:
                    self._json(HTTPStatus.OK, run)
            elif path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
            else:
                self._static(path)
        except (ValueError, TypeError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except BrokenPipeError:
            pass
        except Exception as error:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"request failed: {error}")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        try:
            if path == "/api/experiment/start":
                body = self._body()
                mode = body.get("mode", "paired")
                if not isinstance(mode, str):
                    raise ValueError("mode must be a string")
                self._json(HTTPStatus.ACCEPTED, self.server.service.start(mode))
            elif path == "/api/experiment/pause":
                self._body()
                self._json(HTTPStatus.OK, self.server.service.pause())
            elif path == "/api/experiment/step":
                self._body()
                self._json(HTTPStatus.OK, self.server.service.step())
            elif path == "/api/experiment/reset":
                body = self._body()
                seed = body.get("seed", self.server.service.snapshot()["seed"])
                if isinstance(seed, bool) or not isinstance(seed, int):
                    raise ValueError("seed must be an integer")
                self._json(HTTPStatus.OK, self.server.service.reset(seed))
            else:
                self._body()
                self._error(HTTPStatus.NOT_FOUND, "endpoint not found")
        except RuntimeError as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except (ValueError, TypeError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except BrokenPipeError:
            pass
        except Exception as error:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"request failed: {error}")

    def _static(self, path: str) -> None:
        requested = "index.html" if path == "/" else path.lstrip("/")
        target = (FRONTEND / requested).resolve()
        if FRONTEND.resolve() not in target.parents or not target.is_file():
            # Client-side routes intentionally fall back to the dashboard shell.
            target = FRONTEND / "index.html"
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "dashboard asset not found")
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    database: str | Path = "data/thermal-sentinel.db",
    seed: int = 17,
    tick_seconds: float = 0.65,
) -> SentinelServer:
    store = ExperimentStore(database)
    service = ExperimentService(store, seed=seed, tick_seconds=tick_seconds)
    return SentinelServer((host, port), service)


def main() -> None:
    host = os.getenv("SENTINEL_HOST", "127.0.0.1")
    port = int(os.getenv("SENTINEL_PORT", "8080"))
    database = os.getenv("SENTINEL_DB_PATH", "data/thermal-sentinel.db")
    seed = int(os.getenv("SENTINEL_SEED", "17"))
    tick_seconds = float(os.getenv("SENTINEL_TICK_SECONDS", "0.65"))
    server = create_server(host=host, port=port, database=database, seed=seed, tick_seconds=tick_seconds)

    def stop(_signum: int, _frame: Any) -> None:
        server.service.pause()
        server.shutdown()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_name, stop)
    print(f"Thermal Sentinel ready at http://{host}:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
