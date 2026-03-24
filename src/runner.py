"""
SimulationRunner — lifecycle wrapper around Simulation.

Decouples simulation management from the WebSocket server.
Can be used standalone for batch experiments, CLI, or testing.
"""

from __future__ import annotations
from typing import Optional
import csv
import os

from .config import SimConfig
from .network import RoadNetwork
from .simulation import Simulation
from .state_serializer import serialize_frame, serialize_network


class SimulationRunner:
    """Manages simulation lifecycle: create, reset, step, export."""

    def __init__(self, network: RoadNetwork, config: SimConfig = None):
        self.network = network
        self.config = config or SimConfig()
        self._next_vid_offset = 0
        self.sim: Optional[Simulation] = None

    def create(self, num_vehicles: int = None) -> Simulation:
        """Create a new simulation instance."""
        n = num_vehicles or self.config.num_vehicles
        self.sim = Simulation(
            self.network,
            num_vehicles=n,
            vid_offset=self._next_vid_offset,
            config=self.config,
        )
        self._next_vid_offset = self.sim._next_vid
        return self.sim

    def reset(self, num_vehicles: int = None) -> Simulation:
        """Reset: clear all state and create fresh simulation."""
        # Clear segment vehicle lists
        for seg in self.network.segments.values():
            seg.vehicles.clear()
        return self.create(num_vehicles)

    def step(self, n: int = 1):
        """Advance simulation by n steps."""
        if self.sim is None:
            raise RuntimeError("No simulation created. Call create() first.")
        for _ in range(n):
            self.sim.step()

    def get_frame(self) -> dict:
        """Get current state as a serialized frame."""
        if self.sim is None:
            return {}
        return serialize_frame(self.sim)

    def get_network_json(self) -> dict:
        """Get static network data (roads, signals, indicators)."""
        if self.sim is None:
            return serialize_network(self.network)
        return serialize_network(self.network, self.sim.light_mgr)

    def export_trajectories(self, path: str, duration: float = None, step_interval: int = 1):
        """Export vehicle trajectories to CSV.

        Args:
            path: output CSV file path
            duration: how long to run (seconds). If None, exports current state only.
            step_interval: record every N steps (default: every step)
        """
        if self.sim is None:
            raise RuntimeError("No simulation created. Call create() first.")

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "time", "vid", "speed_ms", "accel", "pos", "lane",
                "route_idx", "segment_u", "segment_v", "active",
            ])

            def write_snapshot():
                for v in self.sim.vehicles.values():
                    seg = v.current_segment
                    writer.writerow([
                        round(self.sim.time, 2),
                        v.vid,
                        round(v.speed, 3),
                        round(v.accel, 3),
                        round(v.pos, 2),
                        v.lane,
                        v.route_idx,
                        seg.u if seg else "",
                        seg.v if seg else "",
                        v.active,
                    ])

            if duration is None:
                write_snapshot()
            else:
                steps = int(duration / self.sim.dt)
                for i in range(steps):
                    self.sim.step()
                    if i % step_interval == 0:
                        write_snapshot()

        return path
