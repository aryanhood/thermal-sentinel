"""Scenario definition: the immutable inputs shared by every policy in a paired run."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

__all__ = ["DEFAULT_BAYS", "Bay", "Scenario", "make_scenario", "seeded_rng"]


@dataclass(frozen=True, slots=True)
class Bay:
    """An inspection station on the floor plan. Coordinates are in metres."""

    id: str
    label: str
    x: float
    y: float


DEFAULT_BAYS: tuple[Bay, ...] = (
    Bay("B1", "Bay 01", 2.0, 1.0),
    Bay("B2", "Bay 02", 5.0, 1.0),
    Bay("B3", "Bay 03", 8.0, 1.0),
    Bay("B4", "Bay 04", 8.0, 5.0),
    Bay("B5", "Bay 05", 5.0, 5.0),
    Bay("B6", "Bay 06", 2.0, 5.0),
)


def seeded_rng(seed: int, *stream: object) -> random.Random:
    """Return an independent, platform-stable RNG for one named stream.

    Streams are derived with SHA-256 so that adding a new stream never shifts the
    values of an existing one, which keeps published benchmark numbers stable.
    """
    key = ":".join(str(part) for part in (seed, *stream)).encode()
    return random.Random(int.from_bytes(hashlib.sha256(key).digest()[:8], "big"))


@dataclass(frozen=True, slots=True)
class Scenario:
    """Everything about the world that a policy must not be able to influence."""

    seed: int
    fault_bay_id: str | None = "B5"
    fault_onset_s: float = 30.0
    fault_rise_c_per_min: float = 12.0
    fault_max_rise_c: float = 15.0
    ambient_c: float = 28.0
    baseline_spread_c: float = 0.3
    line_noise_c: float = 0.35
    line_glitch_prob: float = 0.0005
    line_glitch_c: float = 3.0
    line_glitch_s: float = 3.0
    camera_noise_c: float = 0.25
    close_noise_c: float = 0.08
    robot_speed_m_s: float = 1.0
    scan_s: float = 4.0
    close_inspection_s: float = 6.0
    max_unseen_s: float = 90.0
    horizon_s: float = 180.0
    dt_s: float = 0.5
    dock: tuple[float, float] = (0.0, 3.0)
    bays: tuple[Bay, ...] = field(default=DEFAULT_BAYS)

    def __post_init__(self) -> None:
        ids = [bay.id for bay in self.bays]
        if not ids:
            raise ValueError("scenario needs at least one bay")
        if len(set(ids)) != len(ids):
            raise ValueError("bay ids must be unique")
        if self.fault_bay_id is not None and self.fault_bay_id not in ids:
            raise ValueError(f"unknown fault bay {self.fault_bay_id!r}")
        if not 0 <= self.seed <= 2**31 - 1:
            raise ValueError("seed must be in [0, 2^31 - 1]")
        positive = {
            "robot_speed_m_s": self.robot_speed_m_s,
            "scan_s": self.scan_s,
            "close_inspection_s": self.close_inspection_s,
            "max_unseen_s": self.max_unseen_s,
            "horizon_s": self.horizon_s,
            "dt_s": self.dt_s,
            "fault_rise_c_per_min": self.fault_rise_c_per_min,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if min(self.line_noise_c, self.camera_noise_c, self.close_noise_c) < 0:
            raise ValueError("noise levels cannot be negative")
        if not 0 <= self.line_glitch_prob <= 1:
            raise ValueError("line_glitch_prob must be a probability")
        if not 0 <= self.fault_onset_s < self.horizon_s:
            raise ValueError("fault onset must fall inside the mission horizon")
        for duration in (self.scan_s, self.close_inspection_s, self.horizon_s):
            if abs(round(duration / self.dt_s) * self.dt_s - duration) > 1e-9:
                raise ValueError("durations must be whole multiples of dt_s")

    @property
    def ticks(self) -> int:
        return round(self.horizon_s / self.dt_s)

    def bay(self, bay_id: str) -> Bay:
        for bay in self.bays:
            if bay.id == bay_id:
                return bay
        raise KeyError(bay_id)

    @property
    def case_id(self) -> str:
        return f"seed-{self.seed}-{(self.fault_bay_id or 'none').lower()}"


def make_scenario(seed: int, fault_bay_id: str | None = "B5", **overrides: object) -> Scenario:
    """Build a benchmark scenario whose fault onset time is drawn from the seed.

    Randomising onset means the fault appears at a different phase of the fixed
    route in every case, so neither policy is favoured by a hand-picked start.
    """
    onset = round(seeded_rng(seed, "onset").uniform(10.0, 70.0) * 2) / 2
    values: dict[str, object] = {"seed": seed, "fault_bay_id": fault_bay_id, "fault_onset_s": onset}
    values.update(overrides)
    return Scenario(**values)  # type: ignore[arg-type]
