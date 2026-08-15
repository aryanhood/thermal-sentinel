from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from thermal_sentinel.store import ExperimentStore


class ExperimentStoreTests(unittest.TestCase):
    def test_round_trip_and_digest_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ExperimentStore(Path(directory) / "experiments.db")
            result = {
                "run_id": "test-run",
                "seed": 2026,
                "comparison": {"confirmation_lead_seconds": 18.5},
                "events": [{"at": 1, "kind": "started"}],
            }
            first = store.save(result)
            second = store.save(result)

            self.assertEqual(first["digest"], second["digest"])
            self.assertEqual(store.get("test-run")["result"], result)
            self.assertEqual(store.list()[0]["id"], "test-run")

    def test_missing_run_returns_none_and_limit_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ExperimentStore(Path(directory) / "experiments.db")
            self.assertIsNone(store.get("missing"))
            self.assertEqual(store.list(limit=10_000), [])


if __name__ == "__main__":
    unittest.main()
