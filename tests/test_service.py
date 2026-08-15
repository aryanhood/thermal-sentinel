from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from thermal_sentinel.service import ExperimentService
from thermal_sentinel.store import ExperimentStore


class ExperimentServiceTests(unittest.TestCase):
    def make_service(self, directory: str) -> ExperimentService:
        return ExperimentService(ExperimentStore(Path(directory) / "runs.db"), seed=17, tick_seconds=0.01)

    def test_manual_run_completes_and_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(directory)
            while not service.done:
                service.step()
            snapshot = service.snapshot()
            self.assertEqual(snapshot["status"], "complete")
            self.assertEqual(snapshot["fixed"]["kpis"]["coverage_pct"], 100.0)
            self.assertEqual(snapshot["adaptive"]["kpis"]["coverage_pct"], 100.0)
            self.assertGreater(snapshot["comparison"]["time_saved_s"], 0)
            self.assertEqual(len(service.store.list()), 1)

    def test_reset_validates_seed_and_cannot_interrupt_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(directory)
            with self.assertRaises(ValueError):
                service.reset(-1)
            service.start()
            with self.assertRaises(RuntimeError):
                service.reset(4)
            service.pause()


if __name__ == "__main__":
    unittest.main()
