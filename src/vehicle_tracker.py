"""
Vehicle-to-segment tracking.

Single source of truth for which vehicles are on which segments.
No other code should directly modify segment.vehicles lists.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .vehicle import Vehicle
    from .network import RoadSegment


class VehicleTracker:
    """Owns the mapping between vehicles and road segments."""

    def __init__(self):
        self.vehicles: Dict[int, "Vehicle"] = {}
        # Redundant index: segment edge_id -> sorted vehicle list (rebuilt per step)
        self._seg_vehicles: Dict[Tuple, List["Vehicle"]] = {}

    def add(self, vehicle: "Vehicle", segment: "RoadSegment"):
        """Register a vehicle on a segment."""
        self.vehicles[vehicle.vid] = vehicle
        segment.vehicles.append(vehicle.vid)

    def remove(self, vid: int) -> Optional["Vehicle"]:
        """Remove a vehicle from tracking entirely."""
        v = self.vehicles.pop(vid, None)
        if v is None:
            return None
        seg = v.current_segment
        if seg and vid in seg.vehicles:
            seg.vehicles.remove(vid)
        return v

    def move(self, vid: int, old_seg: "RoadSegment", new_seg: "RoadSegment"):
        """Move a vehicle from one segment to another."""
        if vid in old_seg.vehicles:
            old_seg.vehicles.remove(vid)
        new_seg.vehicles.append(vid)

    def clear(self, network):
        """Remove all vehicles and clear all segment lists."""
        self.vehicles.clear()
        self._seg_vehicles.clear()
        for seg in network.segments.values():
            seg.vehicles.clear()

    def build_segment_index(self):
        """Build sorted per-segment vehicle lists for leader-finding.
        Call once per simulation step before processing vehicles."""
        self._seg_vehicles.clear()
        # Group active vehicles by their current segment
        for v in self.vehicles.values():
            if not v.active:
                continue
            seg = v.current_segment
            if seg is None:
                continue
            if seg.edge_id not in self._seg_vehicles:
                self._seg_vehicles[seg.edge_id] = []
            self._seg_vehicles[seg.edge_id].append(v)

        # Sort each group by position (ascending)
        for edge_id in self._seg_vehicles:
            self._seg_vehicles[edge_id].sort(key=lambda v: v.pos)

    def get_sorted_vehicles(self, edge_id: Tuple) -> List["Vehicle"]:
        """Get vehicles on a segment, sorted by position (ascending)."""
        return self._seg_vehicles.get(edge_id, [])
