"""
Centralized simulation configuration.

All tunable parameters live here. Can be loaded from a JSON file
or constructed with defaults. Passed through the object graph so
no module needs hardcoded constants.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import json
import os


@dataclass
class IDMConfig:
    """Intelligent Driver Model parameters."""
    v0: float = 13.9       # desired speed (m/s) ~ 50 km/h
    T: float = 1.5         # safe time headway (s)
    a: float = 1.5         # max acceleration (m/s^2)
    b: float = 2.0         # comfortable deceleration (m/s^2)
    s0: float = 3.0        # minimum gap (m)
    delta: float = 4.0     # acceleration exponent


@dataclass
class VehicleConfig:
    """Vehicle physical parameters."""
    length: float = 7.0    # metres
    width: float = 2.0     # metres
    stop_margin: float = 3.0  # metres before stop line
    lane_width: float = 3.5   # metres


@dataclass
class SignalConfig:
    """Traffic signal timing and clustering."""
    green_duration: float = 30.0   # seconds
    yellow_duration: float = 3.0   # seconds
    cluster_radius_m: float = 60.0
    expand_seg_max_m: float = 20.0
    expand_dist_max_m: float = 40.0

    @property
    def cycle_length(self) -> float:
        return 2 * (self.green_duration + self.yellow_duration)


@dataclass
class SimConfig:
    """Top-level simulation configuration."""
    dt: float = 0.5             # time step (seconds)
    num_vehicles: int = 200
    seed: int = 42
    spawn_interval: float = 5.0  # seconds between spawns (on average)

    idm: IDMConfig = field(default_factory=IDMConfig)
    vehicle: VehicleConfig = field(default_factory=VehicleConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)

    @classmethod
    def from_json(cls, path: str) -> "SimConfig":
        """Load config from JSON file, falling back to defaults for missing keys."""
        with open(path) as f:
            data = json.load(f)

        config = cls()
        # Top-level scalars
        for key in ("dt", "num_vehicles", "seed", "spawn_interval"):
            if key in data:
                setattr(config, key, data[key])

        # Nested configs
        if "idm" in data:
            for key, val in data["idm"].items():
                if hasattr(config.idm, key):
                    setattr(config.idm, key, val)
        if "vehicle" in data:
            for key, val in data["vehicle"].items():
                if hasattr(config.vehicle, key):
                    setattr(config.vehicle, key, val)
        if "signal" in data:
            for key, val in data["signal"].items():
                if hasattr(config.signal, key):
                    setattr(config.signal, key, val)

        return config

    def to_dict(self) -> dict:
        """Serialize to dict (for JSON export or API responses)."""
        return {
            "dt": self.dt,
            "num_vehicles": self.num_vehicles,
            "seed": self.seed,
            "spawn_interval": self.spawn_interval,
            "idm": {
                "v0": self.idm.v0, "T": self.idm.T, "a": self.idm.a,
                "b": self.idm.b, "s0": self.idm.s0, "delta": self.idm.delta,
            },
            "vehicle": {
                "length": self.vehicle.length, "width": self.vehicle.width,
                "stop_margin": self.vehicle.stop_margin,
                "lane_width": self.vehicle.lane_width,
            },
            "signal": {
                "green_duration": self.signal.green_duration,
                "yellow_duration": self.signal.yellow_duration,
                "cluster_radius_m": self.signal.cluster_radius_m,
                "expand_seg_max_m": self.signal.expand_seg_max_m,
                "expand_dist_max_m": self.signal.expand_dist_max_m,
                "cycle_length": self.signal.cycle_length,
            },
        }
