"""
Traffic light model.

Each signalised intersection cycles through phases.
A phase grants green to a subset of incoming road segments
while the rest are red.

Phase cycle (two-phase scheme per intersection):
  Phase 0: roughly N-S movements green  (green_duration seconds)
  Phase 1: roughly E-W movements green  (green_duration seconds)

With a short all-red clearance interval between phases.

Nearby signal nodes (< CLUSTER_RADIUS_M apart) are grouped into a
single controller so they act as one real-world intersection.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple
import math


GREEN_DURATION = 30.0    # seconds each phase is green
YELLOW_DURATION = 3.0    # seconds of yellow/clearance
CYCLE_LENGTH = 2 * (GREEN_DURATION + YELLOW_DURATION)

CLUSTER_RADIUS_M = 60.0  # merge signal nodes closer than this


def _bearing(x1, y1, x2, y2) -> float:
    """Bearing in degrees (0=North, 90=East) from point 1 to point 2.
    x = longitude, y = latitude."""
    dx = math.radians(x2 - x1) * math.cos(math.radians((y1 + y2) / 2))
    dy = math.radians(y2 - y1)
    return math.degrees(math.atan2(dx, dy)) % 360


class TrafficLight:
    """
    Two-phase traffic light at a single intersection (or cluster).

    Incoming segments are split into two phase groups by their approach
    bearing: roughly N-S vs roughly E-W.
    """

    def __init__(self, node_ids: List[int], incoming_segments,
                 network, offset: float = 0.0):
        self.node_ids = node_ids   # all OSM nodes controlled by this light
        self.offset = offset % CYCLE_LENGTH

        # Compute bearing for each incoming segment and split into two phases
        self._phase_segs: List[Set] = [set(), set()]
        for seg in incoming_segments:
            # bearing from source node to this intersection
            u_inter = network.intersections[seg.u]
            v_inter = network.intersections[seg.v]
            b = _bearing(u_inter.x, u_inter.y, v_inter.x, v_inter.y)
            # Normalise to 0-180 (opposing directions are the same phase)
            b_norm = b % 180
            # 45-135 = roughly E-W (phase 1), else N-S (phase 0)
            if 45 <= b_norm < 135:
                self._phase_segs[1].add(seg.edge_id)
            else:
                self._phase_segs[0].add(seg.edge_id)

        # If one phase is empty, put everything in phase 0
        if not self._phase_segs[0] and self._phase_segs[1]:
            self._phase_segs[0] = self._phase_segs[1]
            self._phase_segs[1] = set()

        # Apply offset to stagger signal timing across intersections
        phase_duration = GREEN_DURATION + YELLOW_DURATION
        offset_in_cycle = self.offset % CYCLE_LENGTH
        self._current_phase = int(offset_in_cycle // phase_duration) % len(self._phase_segs)
        elapsed_in_phase = offset_in_cycle % phase_duration
        if elapsed_in_phase < GREEN_DURATION:
            self._state = "green"
            self._elapsed = elapsed_in_phase
        else:
            self._state = "yellow"
            self._elapsed = elapsed_in_phase - GREEN_DURATION

    # ------------------------------------------------------------------
    def step(self, dt: float):
        """Advance the light by dt seconds."""
        self._elapsed += dt
        if self._state == "green" and self._elapsed >= GREEN_DURATION:
            self._elapsed -= GREEN_DURATION
            self._state = "yellow"
        elif self._state == "yellow" and self._elapsed >= YELLOW_DURATION:
            self._elapsed -= YELLOW_DURATION
            self._state = "green"
            self._current_phase = (self._current_phase + 1) % len(self._phase_segs)

    def is_green(self, edge_id: Tuple) -> bool:
        """Return True if the given incoming edge currently has green."""
        if self._state == "yellow":
            return False
        return edge_id in self._phase_segs[self._current_phase]

    @property
    def state(self) -> str:
        return self._state

    @property
    def current_phase(self) -> int:
        return self._current_phase

    def phase_for_edge(self, edge_id: Tuple) -> int:
        """Return which phase (0 or 1) this edge belongs to, or -1."""
        for i, phase_set in enumerate(self._phase_segs):
            if edge_id in phase_set:
                return i
        return -1

    def time_until_green(self, edge_id: Tuple) -> float:
        """Approximate seconds until this edge gets green (for IDM look-ahead)."""
        if self.is_green(edge_id):
            return 0.0
        if self._state == "green":
            return (GREEN_DURATION - self._elapsed) + YELLOW_DURATION
        else:
            return YELLOW_DURATION - self._elapsed


# ---------------------------------------------------------------------- manager

def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Distance in metres between two lat/lon points."""
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _cluster_signal_nodes(network, radius_m: float = CLUSTER_RADIUS_M):
    """
    Group signal nodes that are within `radius_m` of each other.
    Returns a list of lists (each inner list = one cluster of node ids).
    Uses union-find for transitive clustering: if A↔B and B↔C, all three
    join the same cluster even if A and C are > radius_m apart.
    """
    signal_nodes = [
        (nid, inter) for nid, inter in network.intersections.items()
        if inter.is_signal and inter.incoming
    ]
    if not signal_nodes:
        return []

    # Union-find
    parent = {nid: nid for nid, _ in signal_nodes}
    rank = {nid: 0 for nid, _ in signal_nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    # Merge all pairs within radius
    for i in range(len(signal_nodes)):
        for j in range(i + 1, len(signal_nodes)):
            nid_i, inter_i = signal_nodes[i]
            nid_j, inter_j = signal_nodes[j]
            d = _haversine(inter_i.y, inter_i.x, inter_j.y, inter_j.x)
            if d <= radius_m:
                union(nid_i, nid_j)

    # Group by root
    clusters_dict: Dict[int, List[int]] = {}
    for nid, _ in signal_nodes:
        root = find(nid)
        clusters_dict.setdefault(root, []).append(nid)
    return list(clusters_dict.values())


EXPAND_SEG_MAX_M = 20.0   # only follow segments shorter than this
EXPAND_DIST_MAX_M = 40.0  # max total distance from nearest signal node


def _expand_cluster(cluster: List[int], network, seg_max=EXPAND_SEG_MAX_M,
                    dist_max=EXPAND_DIST_MAX_M) -> List[int]:
    """
    Expand a signal cluster to include nearby non-signal nodes that are
    part of the same physical intersection.

    BFS outward from signal nodes along short segments (< seg_max).
    A non-signal node is absorbed if:
      - it is reachable within dist_max total distance, AND
      - it has 3+ connections (a junction node, not mid-road geometry)
    This prevents chain-expansion along roads with many short segments.
    """
    signal_set = set(cluster)
    expanded = set(cluster)
    # BFS queue: (node_id, distance_from_nearest_signal)
    queue = [(nid, 0.0) for nid in cluster]
    visited = set(cluster)

    while queue:
        nid, dist = queue.pop(0)
        inter = network.intersections.get(nid)
        if inter is None:
            continue

        # Follow both incoming and outgoing segments
        neighbors = []
        for seg in inter.outgoing:
            if seg.length < seg_max:
                neighbors.append((seg.v, seg.length))
        for seg in inter.incoming:
            if seg.length < seg_max:
                neighbors.append((seg.u, seg.length))

        for neighbor_nid, seg_len in neighbors:
            if neighbor_nid in visited:
                continue
            new_dist = dist + seg_len
            if new_dist > dist_max:
                continue
            visited.add(neighbor_nid)

            neighbor_inter = network.intersections.get(neighbor_nid)
            if neighbor_inter is None:
                continue

            # Count connections (incoming + outgoing unique edges)
            n_connections = len(neighbor_inter.incoming) + len(neighbor_inter.outgoing)

            # Absorb if it's a junction node (3+ connections) or a signal node
            if n_connections >= 3 or neighbor_inter.is_signal:
                expanded.add(neighbor_nid)
                queue.append((neighbor_nid, new_dist))

    return list(expanded)


class TrafficLightManager:
    """Owns all traffic lights in the network.

    Nearby signal nodes are clustered into a single TrafficLight controller
    so they behave as one real-world intersection.  Clusters are then expanded
    to include nearby junction nodes connected by short segments, so the
    controller covers the full physical intersection.
    """

    def __init__(self, network):
        self.lights: Dict[int, TrafficLight] = {}  # node_id -> TrafficLight
        self._controllers: List[TrafficLight] = []  # unique controllers
        self._build(network)

    def _build(self, network):
        import random
        rng = random.Random(42)

        clusters = _cluster_signal_nodes(network)

        total_signal = sum(len(c) for c in clusters)
        total_expanded = 0

        for cluster in clusters:
            # Expand cluster to include nearby junction nodes
            expanded = _expand_cluster(cluster, network)
            total_expanded += len(expanded) - len(cluster)

            # Gather all incoming segments for every node in the expanded cluster
            all_incoming = []
            for nid in expanded:
                all_incoming.extend(network.intersections[nid].incoming)

            offset = rng.uniform(0, CYCLE_LENGTH)
            light = TrafficLight(expanded, all_incoming, network, offset)
            self._controllers.append(light)

            # Map every node in the expanded cluster to this shared controller
            for nid in expanded:
                self.lights[nid] = light

        n_clustered = sum(1 for c in clusters if len(c) > 1)
        print(f"TrafficLightManager: {len(self._controllers)} controllers "
              f"({n_clustered} clusters of 2+ nodes, "
              f"{total_signal} signal nodes, "
              f"{total_expanded} junction nodes absorbed)")

    def step(self, dt: float):
        # Only step unique controllers (not duplicates from clustering)
        for ctrl in self._controllers:
            ctrl.step(dt)

    def is_green(self, node_id: int, incoming_edge_id: Tuple) -> bool:
        light = self.lights.get(node_id)
        if light is None:
            return True
        # Intra-cluster segment: never stop inside an intersection
        source_node = incoming_edge_id[0]
        if source_node in self.lights and self.lights[source_node] is light:
            return True
        return light.is_green(incoming_edge_id)

    def time_until_green(self, node_id: int, incoming_edge_id: Tuple) -> float:
        light = self.lights.get(node_id)
        if light is None:
            return 0.0
        return light.time_until_green(incoming_edge_id)
