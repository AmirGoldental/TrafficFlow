"""
Road network wrapper.
Converts the OSMnx MultiDiGraph into a simpler representation
used by the simulation:
  - RoadSegment: directed edge with physical properties
  - Intersection: node with list of incoming/outgoing segments
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import networkx as nx


DEFAULT_SPEED_MS = 50 / 3.6   # 50 km/h in m/s
DEFAULT_LANES = 1


@dataclass
class RoadSegment:
    edge_id: Tuple          # (u, v, key)
    u: int                  # source node id
    v: int                  # target node id
    length: float           # metres
    speed_limit: float      # m/s
    lanes: int              # number of lanes
    name: str = ""

    # Simulation state: list of vehicle ids currently on this segment
    vehicles: List[int] = field(default_factory=list)

    @property
    def travel_time(self) -> float:
        return self.length / max(self.speed_limit, 0.1)


@dataclass
class Intersection:
    node_id: int
    x: float            # longitude
    y: float            # latitude
    is_signal: bool = False
    incoming: List[RoadSegment] = field(default_factory=list)
    outgoing: List[RoadSegment] = field(default_factory=list)


class RoadNetwork:
    def __init__(self, G, signal_nodes: set):
        self.G = G
        self.signal_nodes = signal_nodes
        self.segments: Dict[Tuple, RoadSegment] = {}
        self.intersections: Dict[int, Intersection] = {}
        self._build()

    def _build(self):
        # Build intersections
        for node_id, data in self.G.nodes(data=True):
            self.intersections[node_id] = Intersection(
                node_id=node_id,
                x=data["x"],
                y=data["y"],
                is_signal=(node_id in self.signal_nodes),
            )

        # Build segments
        for u, v, key, data in self.G.edges(keys=True, data=True):
            length = float(data.get("length", 1.0))
            speed = self._parse_speed(data.get("speed_kph", None))
            lanes = self._parse_lanes(data.get("lanes", None))
            name = data.get("name", "")
            if isinstance(name, list):
                name = name[0]

            seg = RoadSegment(
                edge_id=(u, v, key),
                u=u,
                v=v,
                length=max(length, 1.0),
                speed_limit=speed,
                lanes=lanes,
                name=name or "",
            )
            self.segments[(u, v, key)] = seg
            self.intersections[u].outgoing.append(seg)
            self.intersections[v].incoming.append(seg)

        # Report defaults usage
        default_speed_count = sum(
            1 for seg in self.segments.values() if seg.speed_limit == DEFAULT_SPEED_MS
        )
        default_lanes_count = sum(
            1 for seg in self.segments.values() if seg.lanes == DEFAULT_LANES
        )
        print(
            f"RoadNetwork built: {len(self.intersections)} intersections, "
            f"{len(self.segments)} segments, "
            f"{sum(1 for i in self.intersections.values() if i.is_signal)} signals"
        )
        if default_speed_count or default_lanes_count:
            print(
                f"  Defaults applied: {default_speed_count} segments used default speed, "
                f"{default_lanes_count} used default lanes"
            )
        self._validate_connectivity()

    def _parse_speed(self, value) -> float:
        if value is None:
            return DEFAULT_SPEED_MS
        try:
            if isinstance(value, list):
                value = value[0]
            return float(value) / 3.6
        except (ValueError, TypeError):
            return DEFAULT_SPEED_MS

    def _parse_lanes(self, value) -> int:
        if value is None:
            return DEFAULT_LANES
        try:
            if isinstance(value, list):
                value = value[0]
            return max(1, int(value))
        except (ValueError, TypeError):
            return DEFAULT_LANES

    def _validate_connectivity(self):
        """Check for disconnected components and warn."""
        components = list(nx.strongly_connected_components(self.G))
        if len(components) > 1:
            sizes = sorted([len(c) for c in components], reverse=True)
            print(
                f"  Warning: {len(components)} disconnected components "
                f"(sizes: {sizes[:5]}{'...' if len(sizes) > 5 else ''}). "
                f"Vehicles may fail to find routes between components."
            )

    def get_segment(self, u: int, v: int) -> Optional[RoadSegment]:
        """Return the first segment from u to v (any key)."""
        try:
            for key in self.G[u][v]:
                eid = (u, v, key)
                if eid in self.segments:
                    return self.segments[eid]
        except KeyError:
            pass
        return None

    def successors(self, node_id: int) -> List[int]:
        return list(self.G.successors(node_id))

    def predecessors(self, node_id: int) -> List[int]:
        return list(self.G.predecessors(node_id))

    def shortest_path(self, origin: int, destination: int) -> List[int]:
        """Return list of node ids forming the shortest path by travel time."""
        try:
            return nx.shortest_path(
                self.G, origin, destination, weight="travel_time"
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound, KeyError):
            return []
