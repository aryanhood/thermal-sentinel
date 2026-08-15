from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from thermal_sentinel.api import create_server


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            database=Path(self.directory.name) / "runs.db",
            tick_seconds=0.01,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.service.pause()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def request(self, path: str, method: str = "GET", body: object | None = None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, method=method, data=data, headers={"Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            return error.code, json.load(error)
        return response.status, json.load(response)

    def test_health_and_experiment_contract(self) -> None:
        status, health = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        status, experiment = self.request("/api/experiment")
        self.assertEqual(status, 200)
        self.assertEqual(len(experiment["adaptive"]["bays"]), 6)

    def test_step_to_completion_persists_run(self) -> None:
        status, experiment = self.request("/api/experiment")
        while experiment["status"] != "complete":
            status, experiment = self.request("/api/experiment/step", "POST", {})
            self.assertEqual(status, 200)
        _, runs = self.request("/api/runs")
        self.assertEqual(len(runs["runs"]), 1)
        self.assertGreater(experiment["comparison"]["time_saved_s"], 0)

    def test_invalid_reset_and_unknown_run_are_structured_errors(self) -> None:
        status, error = self.request("/api/experiment/reset", "POST", {"seed": "bad"})
        self.assertEqual(status, 400)
        self.assertIn("message", error)
        status, error = self.request("/api/runs/missing")
        self.assertEqual(status, 404)

    def test_dashboard_is_served(self) -> None:
        response = urlopen(self.base + "/", timeout=2)
        self.assertEqual(response.status, 200)
        self.assertIn(b"Thermal Sentinel", response.read())


if __name__ == "__main__":
    unittest.main()
