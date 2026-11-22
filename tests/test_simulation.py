from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from thermal_sentinel import Environment, Policy, Scenario, make_scenario, run_pair, run_policy
from thermal_sentinel.simulation import BayStatus, RobotState, Simulation

BAYS = st.sampled_from(["B1", "B2", "B3", "B4", "B5", "B6", None])


def test_pair_is_reproducible() -> None:
    a = run_pair(make_scenario(5, "B4"))
    b = run_pair(make_scenario(5, "B4"))
    assert a.fixed.to_dict() == b.fixed.to_dict()
    assert a.adaptive.to_dict() == b.adaptive.to_dict()


def test_policies_share_the_same_environment() -> None:
    scenario = make_scenario(9, "B2")
    env = Environment(scenario)
    standalone = run_policy(scenario, "fixed")
    shared = run_policy(scenario, Policy.FIXED, env=env)
    assert standalone.to_dict() == shared.to_dict()


def test_simulation_cannot_run_twice() -> None:
    sim = Simulation(Environment(make_scenario(1)), Policy.FIXED)
    sim.run()
    with pytest.raises(RuntimeError):
        sim.run()


def test_fixed_policy_ignores_telemetry() -> None:
    result = run_policy(make_scenario(3, "B5"), "fixed")
    assert result.metrics["retargets"] == 0
    assert {d["mode"] for d in result.decisions} == {"patrol"}


def test_adaptive_interrupts_route_for_alert() -> None:
    result = run_policy(make_scenario(3, "B5"), "adaptive")
    assert any(d["mode"] == "alert" for d in result.decisions)
    assert result.metrics["detected"]


def test_adaptive_is_faster_on_a_known_case() -> None:
    pair = run_pair(make_scenario(3, "B5"))
    assert pair.time_saved_s is not None
    assert pair.time_saved_s > 0


def test_fixed_route_order() -> None:
    result = run_policy(make_scenario(1, None), "fixed")
    visited = [d["bay"] for d in result.decisions][:8]
    assert visited == ["B1", "B2", "B3", "B4", "B5", "B6", "B1", "B2"]


def test_frames_are_columnar_and_aligned() -> None:
    scenario = make_scenario(2, "B1")
    frames = run_policy(scenario, "adaptive").frames
    lengths = {len(column) for column in frames.values()}
    assert lengths == {scenario.ticks + 1}
    assert frames["t"][0] == 0.0
    assert frames["t"][-1] == scenario.horizon_s
    assert all(len(row) == len(scenario.bays) for row in frames["status"])
    assert set(frames["state"]) <= {int(s) for s in RobotState}


def test_confirmed_bay_is_marked_in_frames() -> None:
    frames = run_policy(make_scenario(3, "B5"), "adaptive").frames
    assert frames["status"][-1][4] == int(BayStatus.CONFIRMED)


@pytest.mark.parametrize("policy", [Policy.GREEDY_EWMA, Policy.CUSUM, Policy.BAYESIAN])
def test_extended_policies_detect_fault_with_zero_false_confirmations(policy: Policy) -> None:
    env = Environment(make_scenario(3, "B5"))
    res = run_policy(env.scenario, policy, env=env)
    assert res.metrics["detected"]
    assert res.metrics["false_confirmations"] == 0
    assert res.metrics["time_to_confirm_s"] is not None
    assert any(d["mode"] == "alert" for d in res.decisions)


def test_cusum_and_bayesian_reject_isolated_glitches() -> None:
    ewma_fa = 0
    cusum_fa = 0
    bayes_fa = 0
    for seed in range(1, 11):
        scenario = Scenario(seed=seed, fault_bay_id=None, line_glitch_prob=0.002)
        env = Environment(scenario)
        ewma_res = run_policy(scenario, Policy.ADAPTIVE, env=env)
        cusum_res = run_policy(scenario, Policy.CUSUM, env=env)
        bayes_res = run_policy(scenario, Policy.BAYESIAN, env=env)
        ewma_fa += int(ewma_res.metrics["false_alarms"])
        cusum_fa += int(cusum_res.metrics["false_alarms"])
        bayes_fa += int(bayes_res.metrics["false_alarms"])
        assert cusum_res.metrics["false_confirmations"] == 0
        assert bayes_res.metrics["false_confirmations"] == 0
    assert ewma_fa > 0
    assert cusum_fa < ewma_fa
    assert bayes_fa < ewma_fa


def test_glitch_only_world_produces_false_alarm_but_never_false_confirmation() -> None:
    scenario = Scenario(seed=11, fault_bay_id=None, line_glitch_prob=0.02)
    result = run_policy(scenario, "adaptive")
    assert result.metrics["line_alerts"] > 0
    assert result.metrics["false_alarms"] > 0
    assert result.metrics["false_confirmations"] == 0
    assert not result.metrics["detected"]


def test_events_are_time_ordered_and_bookended() -> None:
    events = run_policy(make_scenario(4, "B6"), "adaptive").events
    times = [e["t"] for e in events]
    assert times == sorted(times)
    assert events[0]["type"] == "mission_started"
    assert events[-1]["type"] == "mission_ended"


@settings(max_examples=40, deadline=None)
@given(seed=st.integers(min_value=0, max_value=2**31 - 1), bay=BAYS)
def test_invariants_hold_for_any_seed(seed: int, bay: str | None) -> None:
    pair = run_pair(make_scenario(seed, bay))
    for run in (pair.fixed, pair.adaptive):
        m = run.metrics
        assert m["false_confirmations"] == 0
        assert m["guardrail_ok"]
        assert m["distance_m"] >= 0
        assert m["detected"] == (m["time_to_confirm_s"] is not None)
        if bay is None:
            assert not m["detected"]
        else:
            assert m["detected"], "both policies must eventually confirm a real fault"
            assert m["time_to_confirm_s"] > 0
        distances = run.frames["distance"]
        assert distances == sorted(distances)
        for x, y in zip(run.frames["x"], run.frames["y"], strict=True):
            assert -0.01 <= x <= 8.01
            assert 0.99 <= y <= 5.01
