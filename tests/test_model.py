from __future__ import annotations

import pytest

from thermal_sentinel import DEFAULT_BAYS, Environment, Scenario, make_scenario
from thermal_sentinel.model import seeded_rng


def test_make_scenario_is_deterministic() -> None:
    assert make_scenario(7, "B2") == make_scenario(7, "B2")


def test_onset_varies_with_seed_and_stays_in_range() -> None:
    onsets = {make_scenario(seed).fault_onset_s for seed in range(1, 40)}
    assert len(onsets) > 10
    assert all(10.0 <= onset <= 70.0 for onset in onsets)


def test_seed_zero_is_respected() -> None:
    assert make_scenario(0).seed == 0


def test_named_streams_are_independent() -> None:
    assert seeded_rng(1, "a").random() != seeded_rng(1, "b").random()
    assert seeded_rng(1, "a").random() == seeded_rng(1, "a").random()


@pytest.mark.parametrize(
    ("overrides", "pattern"),
    [
        ({"fault_bay_id": "B9"}, "unknown fault bay"),
        ({"seed": -1}, "seed must be"),
        ({"robot_speed_m_s": 0}, "must be positive"),
        ({"line_noise_c": -0.1}, "cannot be negative"),
        ({"line_glitch_prob": 1.5}, "probability"),
        ({"fault_onset_s": 500.0}, "horizon"),
        ({"scan_s": 4.2}, "whole multiples"),
        ({"bays": ()}, "at least one bay"),
        ({"bays": (DEFAULT_BAYS[0], DEFAULT_BAYS[0])}, "unique"),
    ],
)
def test_invalid_scenarios_are_rejected(overrides: dict[str, object], pattern: str) -> None:
    with pytest.raises(ValueError, match=pattern):
        Scenario(**{"seed": 1, **overrides})  # type: ignore[arg-type]


def test_unknown_bay_lookup_raises() -> None:
    with pytest.raises(KeyError):
        Scenario(seed=1).bay("nope")


def test_fault_signal_ramps_then_saturates() -> None:
    env = Environment(Scenario(seed=1, fault_bay_id="B3", fault_onset_s=20.0))
    assert env.fault_rise_c("B3", 19.5) == 0.0
    assert env.fault_rise_c("B3", 25.0) == pytest.approx(1.0)
    assert env.fault_rise_c("B3", 179.0) == 15.0
    assert env.fault_rise_c("B1", 100.0) == 0.0


def test_no_fault_scenario_has_flat_truth() -> None:
    env = Environment(Scenario(seed=4, fault_bay_id=None))
    assert all(env.fault_rise_c(b, 150.0) == 0.0 for b in env.bay_ids)


def test_case_id_format() -> None:
    assert make_scenario(12, None).case_id == "seed-12-none"
    assert make_scenario(12, "B4").case_id == "seed-12-b4"
