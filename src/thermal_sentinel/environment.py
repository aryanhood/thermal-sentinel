"""Ground-truth thermal world and sensor models.

The environment is computed once per scenario, before any policy runs. Both
policies therefore read *identical* line-sensor streams and identical camera
noise for a given (bay, tick), which is what makes the comparison paired.
"""

from __future__ import annotations

from .model import Scenario, seeded_rng

__all__ = ["Environment"]


class Environment:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.bay_ids = tuple(bay.id for bay in scenario.bays)
        base_rng = seeded_rng(scenario.seed, "baseline")
        self.baseline_c = {
            bay_id: scenario.ambient_c + base_rng.gauss(0.0, scenario.baseline_spread_c) for bay_id in self.bay_ids
        }
        self._line = self._sample_line_sensors()

    # -- ground truth -----------------------------------------------------------------
    def fault_rise_c(self, bay_id: str, t: float) -> float:
        """Temperature above baseline caused by the injected fault (0 elsewhere)."""
        s = self.scenario
        if bay_id != s.fault_bay_id or t < s.fault_onset_s:
            return 0.0
        return min(s.fault_rise_c_per_min * (t - s.fault_onset_s) / 60.0, s.fault_max_rise_c)

    def true_temp_c(self, bay_id: str, t: float) -> float:
        return self.baseline_c[bay_id] + self.fault_rise_c(bay_id, t)

    # -- sensors --------------------------------------------------------------------------
    def _sample_line_sensors(self) -> dict[str, list[float]]:
        """Fixed line thermocouples: noisy, plus rare transient glitches (false alarms)."""
        s = self.scenario
        glitch_ticks = round(s.line_glitch_s / s.dt_s)
        readings: dict[str, list[float]] = {}
        for bay_id in self.bay_ids:
            noise = seeded_rng(s.seed, "line", bay_id)
            glitch = seeded_rng(s.seed, "glitch", bay_id)
            remaining = 0
            series: list[float] = []
            for tick in range(s.ticks + 1):
                if remaining == 0 and glitch.random() < s.line_glitch_prob:
                    remaining = glitch_ticks
                spike = s.line_glitch_c if remaining > 0 else 0.0
                remaining = max(0, remaining - 1)
                t = tick * s.dt_s
                series.append(self.true_temp_c(bay_id, t) + spike + noise.gauss(0.0, s.line_noise_c))
            readings[bay_id] = series
        return readings

    def line_delta_c(self, bay_id: str, tick: int) -> float:
        """Line reading minus the commissioned baseline for that bay."""
        return self._line[bay_id][tick] - self.baseline_c[bay_id]

    def camera_delta_c(self, bay_id: str, tick: int, *, close: bool) -> float:
        """Robot thermal-camera reading minus baseline at the end of an inspection."""
        s = self.scenario
        sigma = s.close_noise_c if close else s.camera_noise_c
        noise = seeded_rng(s.seed, "camera", bay_id, tick, int(close)).gauss(0.0, sigma)
        return self.fault_rise_c(bay_id, tick * s.dt_s) + noise
