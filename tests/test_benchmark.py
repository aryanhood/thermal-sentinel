import contextlib
import io
import json
import unittest

from thermal_sentinel.benchmark import (
    DEFAULT_FAULT_BAYS,
    DEFAULT_SEEDS,
    main,
    run_benchmark,
)


class BenchmarkTests(unittest.TestCase):
    def test_full_report_is_deterministic_and_json_serializable(self):
        first = run_benchmark()
        second = run_benchmark()

        self.assertEqual(first, second)
        self.assertEqual(json.loads(json.dumps(first)), first)

    def test_report_has_24_case_matrix_and_aggregate_evidence(self):
        report = run_benchmark()
        benchmark = report["benchmark"]
        summary = report["summary"]
        scenarios = report["scenarios"]

        self.assertEqual(benchmark["scenario_count"], 24)
        self.assertEqual(len(DEFAULT_SEEDS) * len(DEFAULT_FAULT_BAYS), 24)
        self.assertEqual(len(scenarios), 24)
        self.assertEqual(
            {(case["seed"], case["fault_bay_id"]) for case in scenarios},
            {(seed, bay) for seed in DEFAULT_SEEDS for bay in DEFAULT_FAULT_BAYS},
        )

        self.assertIn("median", summary["time_saved_s"])
        self.assertIn("p95", summary["time_saved_s"])
        self.assertGreater(summary["time_saved_s"]["count"], 0)
        self.assertEqual(
            summary["adaptive_wins"]["evaluated_count"],
            24 - summary["outcomes"]["neither_confirmed"],
        )
        self.assertEqual(summary["coverage_pct"]["fixed"]["count"], 24)
        self.assertEqual(summary["coverage_pct"]["adaptive"]["count"], 24)
        self.assertEqual(summary["distance_m"]["fixed"]["count"], 24)
        self.assertEqual(summary["distance_m"]["adaptive"]["count"], 24)

        for case in scenarios:
            self.assertEqual(
                set(case),
                {
                    "case_id",
                    "seed",
                    "fault_bay_id",
                    "outcome",
                    "time_saved_s",
                    "distance_delta_m",
                    "fixed",
                    "adaptive",
                },
            )
            self.assertIn("coverage_pct", case["fixed"])
            self.assertIn("coverage_pct", case["adaptive"])

    def test_cli_emits_parseable_json(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["--compact"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["benchmark"]["scenario_count"], 24)
        self.assertEqual(len(output.getvalue().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
