"""Tests for FollowerService leader-finding."""

from src.config import SimConfig
from src.follower import FollowerService
from src.vehicle import Vehicle
from src.vehicle_tracker import VehicleTracker


class MockLightMgr:
    def __init__(self):
        self.lights = {}

    def is_green(self, node_id, edge_id):
        return True


def make_vehicles(network, config):
    """Create two vehicles on the same segment, same lane."""
    lm = MockLightMgr()
    v1 = Vehicle(vid=1, route=[1, 2, 3], network=network,
                 light_mgr=lm, config=config, speed=5.0)
    v1.pos = 20.0
    v1.lane = 0

    v2 = Vehicle(vid=2, route=[1, 2, 3], network=network,
                 light_mgr=lm, config=config, speed=3.0)
    v2.pos = 50.0
    v2.lane = 0

    return v1, v2, lm


def test_same_segment_leader(mock_network, config):
    v1, v2, lm = make_vehicles(mock_network, config)
    seg = mock_network.get_segment(1, 2)

    tracker = VehicleTracker()
    tracker.add(v1, seg)
    tracker.add(v2, seg)
    tracker.build_segment_index()

    fs = FollowerService(mock_network, lm, config)
    leader = fs.find_leader(v1, tracker)

    assert leader.gap is not None, "Should find a leader"
    assert leader.on_same_segment, "Leader should be on same segment"
    expected_gap = 50.0 - 20.0 - config.vehicle.length
    assert abs(leader.gap - expected_gap) < 0.01, f"Gap should be ~{expected_gap}, got {leader.gap}"
    assert leader.speed == 3.0, "Leader speed should be 3.0"


def test_no_leader_when_alone(mock_network, config):
    lm = MockLightMgr()
    v1 = Vehicle(vid=1, route=[1, 2, 3], network=mock_network,
                 light_mgr=lm, config=config, speed=5.0)
    v1.pos = 20.0
    v1.lane = 0

    seg = mock_network.get_segment(1, 2)
    tracker = VehicleTracker()
    tracker.add(v1, seg)
    tracker.build_segment_index()

    fs = FollowerService(mock_network, lm, config)
    leader = fs.find_leader(v1, tracker)

    assert leader.gap is None, "Should not find a leader when alone"


def test_different_lane_not_leader(mock_network, config):
    v1, v2, lm = make_vehicles(mock_network, config)
    v2.lane = 1  # different lane

    seg = mock_network.get_segment(1, 2)
    tracker = VehicleTracker()
    tracker.add(v1, seg)
    tracker.add(v2, seg)
    tracker.build_segment_index()

    fs = FollowerService(mock_network, lm, config)
    leader = fs.find_leader(v1, tracker)

    assert leader.gap is None, "Vehicle in different lane should not be a leader"


def test_cross_segment_leader(mock_network, config):
    """Vehicle on seg1 should find leader on seg2."""
    lm = MockLightMgr()

    v1 = Vehicle(vid=1, route=[1, 2, 3], network=mock_network,
                 light_mgr=lm, config=config, speed=5.0)
    v1.pos = 90.0  # near end of seg1
    v1.lane = 0

    v2 = Vehicle(vid=2, route=[2, 3], network=mock_network,
                 light_mgr=lm, config=config, speed=2.0)
    v2.pos = 5.0  # near start of seg2
    v2.lane = 0

    seg1 = mock_network.get_segment(1, 2)
    seg2 = mock_network.get_segment(2, 3)

    tracker = VehicleTracker()
    tracker.add(v1, seg1)
    tracker.add(v2, seg2)
    tracker.build_segment_index()

    fs = FollowerService(mock_network, lm, config)
    leader = fs.find_leader(v1, tracker)

    assert leader.gap is not None, "Should find cross-segment leader"
    assert not leader.on_same_segment, "Leader should not be on same segment"
    # Gap = (100 - 90) + 5 - 7 = 8
    expected_gap = (100.0 - 90.0) + 5.0 - config.vehicle.length
    assert abs(leader.gap - expected_gap) < 0.01, f"Expected gap ~{expected_gap}, got {leader.gap}"
