import json
import unittest

from inspection_core import Experiment, default_scenario, run_comparison, run_pair


class EngineTests(unittest.TestCase):
    def test_pair_is_deterministic_and_json_serializable(self):
        first = run_pair(42)
        second = run_pair(42)
        self.assertEqual(first, second)
        json.dumps(first)

    def test_adaptive_confirms_fault_faster(self):
        comparison = run_comparison(seed=17)
        fixed = comparison.fixed.kpis
        adaptive = comparison.adaptive.kpis
        self.assertEqual(fixed["confirmed_hotspots"], 1)
        self.assertEqual(adaptive["confirmed_hotspots"], 1)
        self.assertLess(adaptive["time_to_confirm_s"], fixed["time_to_confirm_s"])
        self.assertGreater(comparison.improvement_pct, 0)

    def test_full_coverage_and_single_close_confirmation(self):
        comparison = run_comparison(seed=9)
        self.assertEqual(comparison.fixed.kpis["coverage_pct"], 100.0)
        self.assertEqual(comparison.fixed.kpis["bays_inspected"], 6)
        self.assertEqual(comparison.fixed.kpis["reinspections"], 1)
        self.assertEqual(comparison.adaptive.kpis["coverage_pct"], 100.0)
        self.assertEqual(comparison.adaptive.kpis["bays_inspected"], 6)
        adaptive_confirmed = [o for o in comparison.adaptive.observations if o["decision"] == "confirmed"]
        self.assertEqual(adaptive_confirmed[0]["bay_id"], "B5")

    def test_adaptive_interrupts_patrol_on_live_alert(self):
        experiment = Experiment(default_scenario(17), "adaptive")
        while not experiment.done:
            experiment.step()
        events = experiment.result.events if experiment.result else ()
        event_types = [event["type"] for event in events]
        self.assertIn("bay_alert", event_types)
        self.assertIn("patrol_interrupted", event_types)
        self.assertIn("alert_resolved", event_types)
        interrupted = [event for event in events if event["type"] == "patrol_interrupted"]
        self.assertTrue(any(item["data"].get("to_bay_id") == "B5" for item in interrupted))
        alert_decisions = [decision for decision in experiment.result.planner_decisions if decision.get("mode") == "alert-response"]
        self.assertTrue(alert_decisions)
        self.assertTrue(all(decision["selected_bay_id"] == "B5" for decision in alert_decisions))

    def test_stepwise_snapshot_has_dashboard_contract(self):
        experiment = Experiment(default_scenario(3), "adaptive")
        snapshot = experiment.step()
        self.assertIn("robot", snapshot)
        self.assertEqual(len(snapshot["bays"]), 6)
        self.assertIn("next_station_id", snapshot["planner"])
        while not experiment.done:
            experiment.step()
        self.assertIsNotNone(experiment.result)
        self.assertTrue(experiment.snapshot()["done"])

    def test_bad_scenario_and_policy_are_rejected(self):
        with self.assertRaises(ValueError):
            default_scenario(fault_bay_id="missing")
        with self.assertRaises(ValueError):
            Experiment(default_scenario(), "fastest")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            default_scenario(robot_speed_m_s=0)
        with self.assertRaises(ValueError):
            default_scenario(sensor_noise_c=-0.1)


if __name__ == "__main__":
    unittest.main()
