"""
Simulation engine.

Each tick:
  1. Advance traffic lights
  2. For each active vehicle, find leader on same segment, call vehicle.step()
  3. Spawn new vehicles at configured rate
  4. Collect statistics
"""

from __future__ import annotations
from typing import Dict, List, Optional
import random
import math

from .config import SimConfig
from .follower import FollowerService
from .network import RoadNetwork
from .traffic_light import TrafficLightManager
from .vehicle import Vehicle
from .vehicle_tracker import VehicleTracker


class Simulation:
    def __init__(
        self,
        network: RoadNetwork,
        num_vehicles: int = 200,
        seed: int = 42,
        dt: float = None,
        vid_offset: int = 0,
        config: SimConfig = None,
    ):
        self.config = config or SimConfig()
        if num_vehicles != 200:
            self.config.num_vehicles = num_vehicles
        if seed != 42:
            self.config.seed = seed

        self.network = network
        self.light_mgr = TrafficLightManager(network, signal_config=self.config.signal)
        self.dt = dt if dt is not None else self.config.dt
        self.time = 0.0
        self.rng = random.Random(self.config.seed)

        self.tracker = VehicleTracker()
        self.follower = FollowerService(network, self.light_mgr, self.config)
        self._next_vid = vid_offset

        # Pre-sample origin/destination node pairs
        self._node_list = list(network.intersections.keys())

        # Spawn initial vehicles
        for _ in range(num_vehicles):
            self._spawn_vehicle()

        self.stats: List[Dict] = []   # capped to last 500 entries
        self._max_stats = 500

    @property
    def vehicles(self) -> Dict[int, Vehicle]:
        """Backward-compatible access to vehicle dict via tracker."""
        return self.tracker.vehicles

    # ------------------------------------------------------------------
    def _random_node(self) -> int:
        return self.rng.choice(self._node_list)

    def _spawn_vehicle(self) -> Optional[Vehicle]:
        for _ in range(20):   # retry if no valid route found
            origin = self._random_node()
            dest = self._random_node()
            if origin == dest:
                continue
            route = self.network.shortest_path(origin, dest)
            if len(route) < 2:
                continue

            vid = self._next_vid
            self._next_vid += 1

            v = Vehicle(
                vid=vid,
                route=route,
                network=self.network,
                light_mgr=self.light_mgr,
                config=self.config,
                speed=self.rng.uniform(0, 5),
            )
            seg = v.current_segment
            if seg is None:
                continue
            v.lane = self.rng.randint(0, seg.lanes - 1)
            # Offset spawn to avoid overlapping existing vehicles at pos=0
            v.pos = self.rng.uniform(0, min(seg.length * 0.5, 20.0))
            self.tracker.add(v, seg)
            return v
        return None

    # ------------------------------------------------------------------
    def step(self):
        """Advance simulation by one dt."""
        self.light_mgr.step(self.dt)

        # Build per-segment sorted vehicle index
        self.tracker.build_segment_index()

        # Step each vehicle
        dead = []
        for vid, v in list(self.vehicles.items()):
            if not v.active:
                dead.append(vid)
                continue

            seg = v.current_segment
            if seg is None:
                dead.append(vid)
                continue

            leader = self.follower.find_leader(v, self.tracker)

            old_seg = seg
            v.step(self.dt, leader.gap, leader.speed, tracker=self.tracker)

            # Hard clamp: never overlap with leader (only on same segment)
            if leader.on_same_segment and leader.pos is not None and v.current_segment == old_seg:
                max_pos = leader.pos - self.config.vehicle.length - 0.5
                if v.pos > max_pos:
                    v.pos = max(max_pos, 0.0)
                    v.speed = min(v.speed, leader.speed or 0.0)

        # Remove completed/stuck vehicles and spawn replacements
        for vid in dead:
            self.tracker.remove(vid)
            self._spawn_vehicle()

        self.time += self.dt
        self._record_stats()

    # ------------------------------------------------------------------
    def _record_stats(self):
        active = [v for v in self.vehicles.values() if v.active]
        if not active:
            return
        avg_speed = sum(v.speed for v in active) / len(active)
        self.stats.append({
            "time": self.time,
            "active_vehicles": len(active),
            "avg_speed_ms": avg_speed,
            "avg_speed_kmh": avg_speed * 3.6,
        })
        if len(self.stats) > self._max_stats:
            self.stats = self.stats[-self._max_stats:]

    def run(self, duration: float):
        """Run simulation for `duration` seconds."""
        steps = int(duration / self.dt)
        for i in range(steps):
            self.step()
            if i % max(1, int(10 / self.dt)) == 0:
                s = self.stats[-1] if self.stats else {}
                print(
                    f"  t={self.time:.1f}s  vehicles={s.get('active_vehicles', 0)}"
                    f"  avg_speed={s.get('avg_speed_kmh', 0):.1f} km/h"
                )
