"""Shared test fixtures for TrafficFlow tests."""

import sys
import os
import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import SimConfig
from src.network import RoadSegment, Intersection, RoadNetwork
from src.traffic_light import TrafficLight, TrafficLightManager
from src.vehicle import Vehicle


class MockNetwork:
    """Minimal network for unit testing — 3 nodes in a line."""

    def __init__(self):
        self.intersections = {}
        self.segments = {}
        self.G = None

        # 3 nodes: 1 -> 2 -> 3, each 100m apart
        self.intersections[1] = Intersection(
            node_id=1, x=-71.08, y=42.32, is_signal=True,
        )
        self.intersections[2] = Intersection(
            node_id=2, x=-71.079, y=42.32, is_signal=True,
        )
        self.intersections[3] = Intersection(
            node_id=3, x=-71.078, y=42.32, is_signal=False,
        )

        seg1 = RoadSegment(
            edge_id=(1, 2, 0), u=1, v=2, length=100.0,
            speed_limit=13.9, lanes=2, name="Test Rd",
        )
        seg2 = RoadSegment(
            edge_id=(2, 3, 0), u=2, v=3, length=100.0,
            speed_limit=13.9, lanes=2, name="Test Rd",
        )

        self.segments[(1, 2, 0)] = seg1
        self.segments[(2, 3, 0)] = seg2

        self.intersections[1].outgoing.append(seg1)
        self.intersections[2].incoming.append(seg1)
        self.intersections[2].outgoing.append(seg2)
        self.intersections[3].incoming.append(seg2)

    def get_segment(self, u, v):
        for key, seg in self.segments.items():
            if seg.u == u and seg.v == v:
                return seg
        return None

    def shortest_path(self, origin, dest):
        # Hardcoded for our 3-node network
        if origin == 1 and dest == 3:
            return [1, 2, 3]
        if origin == 1 and dest == 2:
            return [1, 2]
        if origin == 2 and dest == 3:
            return [2, 3]
        return []


@pytest.fixture
def config():
    return SimConfig()


@pytest.fixture
def mock_network():
    return MockNetwork()
