"""Thread-safe orchestration of a live paired Thermal Sentinel experiment."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict
from typing import Any

from inspection_core import Experiment, default_scenario

from .store import ExperimentStore


class ExperimentService:
    def __init__(self, store: ExperimentStore, *, seed: int = 17, tick_seconds: float = 0.65) -> None:
        self.store = store
        self.tick_seconds = max(0.05, tick_seconds)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._run_id = ""
        self._persisted = False
        self._fixed: Experiment
        self._adaptive: Experiment
        self._scenario: Any
        self._mode = "paired"
        self.reset(seed)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        with self._lock:
            if self.running:
                raise RuntimeError("cannot reset a running experiment")
            selected_seed = self._scenario.seed if hasattr(self, "_scenario") and seed is None else int(seed or 17)
            if not 0 <= selected_seed <= 2_147_483_647:
                raise ValueError("seed must be between 0 and 2147483647")
            self._scenario = default_scenario(selected_seed)
            self._fixed = Experiment(self._scenario, "fixed")
            self._adaptive = Experiment(self._scenario, "adaptive")
            self._run_id = str(uuid.uuid4())
            self._persisted = False
            self._mode = "paired"
            return self.snapshot()

    def start(self, mode: str = "paired") -> dict[str, Any]:
        with self._lock:
            if mode not in {"paired", "fixed", "adaptive"}:
                raise ValueError("mode must be one of: paired, fixed, adaptive")
            if self.done:
                self.reset(self._scenario.seed)
            self._mode = mode
            if not self.running:
                self._thread = threading.Thread(target=self._run_loop, name="sentinel-experiment", daemon=True)
                self._thread.start()
            return self.snapshot()

    def pause(self) -> dict[str, Any]:
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=self.tick_seconds * 2 + 0.2)
        return self.snapshot()

    @property
    def done(self) -> bool:
        if self._mode == "fixed":
            return self._fixed.done
        if self._mode == "adaptive":
            return self._adaptive.done
        return self._fixed.done and self._adaptive.done

    def step(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                raise RuntimeError("pause the experiment before stepping manually")
            self._advance(self._mode)
            return self.snapshot()

    def _run_loop(self) -> None:
        current = threading.current_thread()
        while True:
            with self._lock:
                if self._thread is not current or self.done:
                    break
                self._advance(self._mode)
            time.sleep(self.tick_seconds)
        with self._lock:
            if self._thread is current:
                self._thread = None

    def _advance(self, mode: str = "paired") -> None:
        if mode in {"paired", "fixed"} and not self._fixed.done:
            self._fixed.step()
        if mode in {"paired", "adaptive"} and not self._adaptive.done:
            self._adaptive.step()
        if self.done and not self._persisted:
            self._persist()

    @staticmethod
    def _comparison(fixed: dict[str, Any], adaptive: dict[str, Any]) -> dict[str, Any]:
        fixed_time = fixed["kpis"]["time_to_confirm_s"]
        adaptive_time = adaptive["kpis"]["time_to_confirm_s"]
        time_saved = None if fixed_time is None or adaptive_time is None else round(fixed_time - adaptive_time, 2)
        improvement = None if time_saved is None or not fixed_time else round(time_saved / fixed_time * 100, 1)
        fixed_distance = fixed["kpis"]["distance_m"]
        adaptive_distance = adaptive["kpis"]["distance_m"]
        distance_delta = round(adaptive_distance - fixed_distance, 2)
        distance_delta_pct = round(distance_delta / fixed_distance * 100, 1) if fixed_distance else 0.0
        return {
            "time_saved_s": time_saved,
            "time_to_confirm_pct": improvement,
            "distance_delta_m": distance_delta,
            "distance_delta_pct": distance_delta_pct,
            "both_full_coverage": fixed["kpis"]["coverage_pct"] == 100.0
            and adaptive["kpis"]["coverage_pct"] == 100.0,
            "winner": "adaptive" if time_saved and time_saved > 0 else "fixed" if time_saved and time_saved < 0 else "pending",
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            fixed = self._fixed.snapshot()
            adaptive = self._adaptive.snapshot()
            return {
                "run_id": self._run_id,
                "seed": self._scenario.seed,
                "status": "complete" if self.done else "running" if self.running else "ready",
                "scenario": {
                    "fault_bay_id": self._scenario.fault_bay_id,
                    "fault_onset_s": self._scenario.fault_onset_s,
                    "max_unseen_s": self._scenario.max_unseen_s,
                    "model": "seeded thermal-ramp digital twin",
                },
                "fixed": fixed,
                "adaptive": adaptive,
                "comparison": self._comparison(fixed, adaptive),
            }

    def result(self) -> dict[str, Any]:
        if not self.done:
            raise RuntimeError("experiment is not complete")
        fixed_result = self._fixed.result
        adaptive_result = self._adaptive.result
        assert fixed_result is not None and adaptive_result is not None
        fixed = fixed_result.to_dict()
        adaptive = adaptive_result.to_dict()
        return {
            "run_id": self._run_id,
            "seed": self._scenario.seed,
            "scenario": asdict(self._scenario),
            "fixed": fixed,
            "adaptive": adaptive,
            "comparison": self._comparison(fixed, adaptive),
        }

    def _persist(self) -> None:
        try:
            self.store.save(self.result())
            self._persisted = True
        except Exception:
            # Storage is deliberately outside the autonomy failure boundary.
            self._persisted = False
