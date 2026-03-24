"""
Leader-finding service.

Finds the nearest leader vehicle for each vehicle, handling:
  - Same-segment leader scan
  - Cross-segment spillback lookahead
  - "Don't block the box" (scan past intra-cluster segments)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .vehicle import Vehicle
    from .vehicle_tracker import VehicleTracker
    from .traffic_light import TrafficLightManager
    from .network import RoadNetwork
    from .config import SimConfig


@dataclass
class LeaderInfo:
    """Result of leader-finding for a single vehicle."""
    gap: Optional[float] = None
    speed: Optional[float] = None
    pos: Optional[float] = None       # leader's position (for hard clamp)
    on_same_segment: bool = False


class FollowerService:
    """Finds leaders for all vehicles in a simulation step."""

    def __init__(self, network: "RoadNetwork", light_mgr: "TrafficLightManager",
                 config: "SimConfig"):
        self.network = network
        self.light_mgr = light_mgr
        self.vehicle_length = config.vehicle.length

    def find_leader(self, v: "Vehicle", tracker: "VehicleTracker") -> LeaderInfo:
        """Find the nearest leader for vehicle v."""
        seg = v.current_segment
        if seg is None:
            return LeaderInfo()

        info = self._same_segment_leader(v, seg, tracker)

        # Cross-segment spillback if no leader found on current segment
        if info.gap is None and v.route_idx + 2 < len(v.route):
            cross = self._cross_segment_leader(v, seg, tracker)
            if cross.gap is not None:
                info.gap = cross.gap
                info.speed = cross.speed
                # Don't set pos or on_same_segment — cross-segment leaders
                # are handled by IDM alone, no hard clamp

        return info

    def _same_segment_leader(self, v: "Vehicle", seg, tracker: "VehicleTracker") -> LeaderInfo:
        """Scan sorted vehicles on the same segment for nearest leader in same lane."""
        ordered = tracker.get_sorted_vehicles(seg.edge_id)
        found_self = False
        for other in ordered:
            if other.vid == v.vid:
                found_self = True
                continue
            if found_self and other.lane == v.lane:
                return LeaderInfo(
                    gap=other.pos - v.pos - self.vehicle_length,
                    speed=other.speed,
                    pos=other.pos,
                    on_same_segment=True,
                )
        return LeaderInfo()

    def _cross_segment_leader(self, v: "Vehicle", seg, tracker: "VehicleTracker") -> LeaderInfo:
        """Look ahead along the route to find leaders on upcoming segments.

        Implements 'don't block the box': scans past intra-cluster segments
        to check the exit segment after an intersection.
        """
        lookahead_dist = seg.length - v.pos
        max_lookahead = 5

        for look_i in range(max_lookahead):
            ri = v.route_idx + 1 + look_i
            if ri + 1 >= len(v.route):
                break

            look_u = v.route[ri]
            look_v = v.route[ri + 1]
            look_seg = self.network.get_segment(look_u, look_v)
            if look_seg is None:
                break

            look_ordered = tracker.get_sorted_vehicles(look_seg.edge_id)
            target_lane = min(v.lane, look_seg.lanes - 1)

            for other in look_ordered:
                if other.lane == target_lane:
                    cross_gap = lookahead_dist + other.pos - self.vehicle_length
                    return LeaderInfo(gap=cross_gap, speed=other.speed)

            # Accumulate distance through this segment
            lookahead_dist += look_seg.length

            # Only keep scanning if this is an intra-cluster segment
            # (inside an intersection) — otherwise stop at the first
            # empty segment outside the intersection
            src_light = self.light_mgr.lights.get(look_u)
            dst_light = self.light_mgr.lights.get(look_v)
            if not (src_light is not None and src_light is dst_light):
                break  # not intra-cluster, stop scanning

        return LeaderInfo()
