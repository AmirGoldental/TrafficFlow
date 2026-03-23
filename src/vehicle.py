"""
Vehicle agent using the Intelligent Driver Model (IDM) for longitudinal dynamics.

IDM reference:
  Treiber, Hennecke, Helbing (2000) "Congested traffic states in empirical
  observations and microscopic simulations", Phys. Rev. E 62, 1805.

Each vehicle:
  - Follows a pre-computed route (list of node ids)
  - Tracks its position as (segment, distance_along_segment)
  - Uses IDM to decide acceleration based on gap to the vehicle ahead
    and the distance to the next red light
  - Advances to the next segment when it reaches the end of the current one
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import math

# IDM parameters (tunable)
IDM_V0 = 13.9      # desired speed (m/s) ≈ 50 km/h
IDM_T = 1.5        # safe time headway (s)
IDM_A = 1.5        # max acceleration (m/s²)
IDM_B = 2.0        # comfortable deceleration (m/s²)
IDM_S0 = 3.0       # minimum gap (m) — bumper-to-bumper
IDM_DELTA = 4.0    # acceleration exponent

# Stop-line distance: vehicle stops this far before end of segment at red light
STOP_MARGIN = 3.0  # metres


@dataclass
class Vehicle:
    vid: int
    route: List[int]            # sequence of node ids
    network: object             # RoadNetwork (avoid circular import)
    light_mgr: object           # TrafficLightManager

    # Kinematic state
    speed: float = 0.0          # m/s
    accel: float = 0.0          # m/s²

    # Position
    route_idx: int = 0          # index of current origin node in route
    pos: float = 0.0            # metres travelled along current segment
    lane: int = 0               # current lane (0-indexed from right)

    # Bookkeeping
    active: bool = True
    distance_total: float = 0.0

    # ---------------------------------------------------------------
    @property
    def current_segment(self):
        if self.route_idx + 1 >= len(self.route):
            return None
        u = self.route[self.route_idx]
        v = self.route[self.route_idx + 1]
        return self.network.get_segment(u, v)

    @property
    def current_node(self) -> int:
        return self.route[self.route_idx]

    @property
    def next_node(self) -> Optional[int]:
        if self.route_idx + 1 < len(self.route):
            return self.route[self.route_idx + 1]
        return None

    # ---------------------------------------------------------------
    def step(self, dt: float, leader_gap: Optional[float], leader_speed: Optional[float]):
        """Advance vehicle by dt seconds."""
        if not self.active:
            return

        seg = self.current_segment
        if seg is None:
            self.active = False
            return

        v0 = min(IDM_V0, seg.speed_limit) or IDM_V0  # fallback if speed_limit is 0
        dist_to_end = seg.length - self.pos

        # Check traffic light at end of segment
        red_gap = self._red_light_gap(seg, dist_to_end)

        # Effective gap and leader speed for IDM
        gap, v_lead = self._effective_gap_and_speed(
            leader_gap, leader_speed, red_gap
        )

        self.accel = self._idm_accel(v0, gap, v_lead)
        self.speed = max(0.0, self.speed + self.accel * dt)

        dx = self.speed * dt
        self.pos += dx
        self.distance_total += dx

        # Advance to next segment(s) if past the end — loop handles very short segments
        while self.pos >= seg.length:
            overflow = self.pos - seg.length
            try:
                seg.vehicles.remove(self.vid)
            except ValueError:
                pass  # already removed (e.g. by reset)
            self.route_idx += 1
            self.pos = overflow

            next_seg = self.current_segment
            if next_seg is None:
                self.active = False
                return
            next_seg.vehicles.append(self.vid)
            # Keep current lane, clamped to new segment's lane count
            self.lane = min(self.lane, next_seg.lanes - 1)
            seg = next_seg

    # ---------------------------------------------------------------
    def _red_light_gap(self, seg, dist_to_end: float) -> Optional[float]:
        """
        If the traffic light at the end of the current segment is red,
        return the distance to the stop line; else None.
        """
        next_node = self.next_node
        if next_node is None:
            return None
        if self.light_mgr.is_green(next_node, seg.edge_id):
            return None
        # Red: stop STOP_MARGIN before the end
        gap = dist_to_end - STOP_MARGIN
        return max(gap, 0.0)

    def _effective_gap_and_speed(
        self,
        leader_gap: Optional[float],
        leader_speed: Optional[float],
        red_gap: Optional[float],
    ) -> Tuple[float, float]:
        """
        Pick the more restrictive constraint (closest obstacle).
        A red light is modelled as a stopped virtual vehicle.
        """
        candidates = []
        if leader_gap is not None:
            # Negative gap means overlap — treat as zero gap (emergency brake)
            candidates.append((max(leader_gap, 0.0), leader_speed or 0.0))
        if red_gap is not None:
            candidates.append((red_gap, 0.0))

        if not candidates:
            return (1e6, self.speed)   # free-flow

        return min(candidates, key=lambda c: c[0])

    def _idm_accel(self, v0: float, gap: float, v_lead: float) -> float:
        v = self.speed
        dv = v - v_lead
        s_star = IDM_S0 + max(0.0, v * IDM_T + v * dv / (2 * math.sqrt(IDM_A * IDM_B)))
        s = max(gap, 0.01)
        a = IDM_A * (1.0 - (v / v0) ** IDM_DELTA - (s_star / s) ** 2)
        return max(-IDM_B * 2, min(IDM_A, a))
