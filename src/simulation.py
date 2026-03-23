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

from .network import RoadNetwork
from .traffic_light import TrafficLightManager
from .vehicle import Vehicle

DT = 0.5            # simulation time step (seconds)
SPAWN_INTERVAL = 5  # spawn a new vehicle every N seconds (on average)


class Simulation:
    def __init__(
        self,
        network: RoadNetwork,
        num_vehicles: int = 200,
        seed: int = 42,
        dt: float = DT,
    ):
        self.network = network
        self.light_mgr = TrafficLightManager(network)
        self.dt = dt
        self.time = 0.0
        self.rng = random.Random(seed)

        self.vehicles: Dict[int, Vehicle] = {}
        self._next_vid = 0

        # Pre-sample origin/destination node pairs
        self._node_list = list(network.intersections.keys())
        self._spawn_queue: List[Vehicle] = []

        # Spawn initial vehicles
        for _ in range(num_vehicles):
            self._spawn_vehicle()

        self.stats: List[Dict] = []   # capped to last 500 entries
        self._max_stats = 500

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
                speed=self.rng.uniform(0, 5),
            )
            seg = v.current_segment
            if seg is None:
                continue
            v.lane = self.rng.randint(0, seg.lanes - 1)
            seg.vehicles.append(vid)
            self.vehicles[vid] = v
            return v
        return None

    # ------------------------------------------------------------------
    def step(self):
        """Advance simulation by one dt."""
        self.light_mgr.step(self.dt)

        # Build per-segment sorted vehicle list (by position, ascending)
        seg_vehicles: Dict = {}
        for seg_id, seg in self.network.segments.items():
            if seg.vehicles:
                ordered = sorted(
                    [self.vehicles[vid] for vid in seg.vehicles if vid in self.vehicles],
                    key=lambda v: v.pos,
                )
                seg_vehicles[seg_id] = ordered

        # Step each vehicle
        dead = []
        for vid, v in self.vehicles.items():
            if not v.active:
                dead.append(vid)
                continue

            seg = v.current_segment
            if seg is None:
                dead.append(vid)
                continue

            # Find nearest leader on same segment AND same lane
            # List is sorted ascending by pos; scan from v's position upward
            ordered = seg_vehicles.get(seg.edge_id, [])
            leader_gap = None
            leader_speed = None
            leader_pos = None
            found_self = False
            for other in ordered:
                if other.vid == vid:
                    found_self = True
                    continue
                if found_self and other.lane == v.lane:
                    leader_gap = other.pos - v.pos - 7.0  # 7 m vehicle length
                    leader_speed = other.speed
                    leader_pos = other.pos
                    break

            old_seg = seg
            v.step(self.dt, leader_gap, leader_speed)

            # Hard clamp: never overlap with leader (only if still on same segment)
            if leader_pos is not None and v.current_segment is old_seg:
                max_pos = leader_pos - 7.0 - 0.5  # vehicle length + min buffer
                if v.pos > max_pos:
                    v.pos = max(max_pos, 0.0)
                    v.speed = min(v.speed, leader_speed or 0.0)

        # Remove completed/stuck vehicles and spawn replacements
        for vid in dead:
            v = self.vehicles.pop(vid)
            seg = v.current_segment
            if seg and vid in seg.vehicles:
                seg.vehicles.remove(vid)
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
            if i % (10 / self.dt) == 0:
                s = self.stats[-1] if self.stats else {}
                print(
                    f"  t={self.time:.1f}s  vehicles={s.get('active_vehicles', 0)}"
                    f"  avg_speed={s.get('avg_speed_kmh', 0):.1f} km/h"
                )
