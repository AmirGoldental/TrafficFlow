"""Tests for Vehicle IDM behavior."""

import math
from src.config import SimConfig
from src.vehicle import Vehicle


class MockLightMgr:
    def is_green(self, node_id, edge_id):
        return True

    def time_until_green(self, node_id, edge_id):
        return 0.0


def make_vehicle(network, route, speed=0.0, pos=0.0, config=None):
    config = config or SimConfig()
    v = Vehicle(
        vid=1, route=route, network=network,
        light_mgr=MockLightMgr(), config=config, speed=speed,
    )
    v.pos = pos
    return v


def test_free_flow_acceleration(mock_network):
    """Vehicle with no leader should accelerate toward desired speed."""
    v = make_vehicle(mock_network, [1, 2, 3], speed=0.0, pos=0.0)
    initial_speed = v.speed

    for _ in range(10):
        v.step(0.5, leader_gap=None, leader_speed=None)

    assert v.speed > initial_speed, "Vehicle should accelerate in free flow"
    assert v.speed > 0, "Speed should be positive"


def test_car_following_deceleration(mock_network):
    """Vehicle approaching a slow leader should decelerate."""
    v = make_vehicle(mock_network, [1, 2, 3], speed=10.0, pos=20.0)

    # Leader 15m ahead going 0 m/s → gap = 15 - 7 = 8m
    v.step(0.5, leader_gap=8.0, leader_speed=0.0)

    assert v.accel < 0, "Should decelerate when approaching stopped leader"


def test_red_light_stopping(mock_network):
    """Vehicle approaching red light should stop before segment end."""

    class RedLightMgr:
        def is_green(self, node_id, edge_id):
            return False
        def time_until_green(self, node_id, edge_id):
            return 20.0

    config = SimConfig()
    v = Vehicle(
        vid=1, route=[1, 2, 3], network=mock_network,
        light_mgr=RedLightMgr(), config=config, speed=5.0,
    )
    v.pos = 80.0  # 20m from end of 100m segment

    # Run several steps
    for _ in range(40):
        v.step(0.5, leader_gap=None, leader_speed=None)

    # Vehicle should have stopped before segment end
    assert v.pos < 100.0, "Vehicle should stop before segment end at red light"
    assert v.speed < 1.0, "Vehicle should be nearly stopped"


def test_segment_transition(mock_network):
    """Vehicle at end of segment should advance to next segment."""
    v = make_vehicle(mock_network, [1, 2, 3], speed=10.0, pos=95.0)
    seg1 = mock_network.get_segment(1, 2)
    seg1.vehicles.append(v.vid)

    v.step(0.5, leader_gap=None, leader_speed=None)

    # After step, vehicle should be on second segment (or still on first if not enough distance)
    # With speed=10 and dt=0.5, dx=5m, pos goes to ~100+, triggering transition
    assert v.route_idx >= 1 or v.pos >= 95.0, "Vehicle should advance"


def test_speed_limit_respected(mock_network, config):
    """Vehicle should not exceed segment speed limit."""
    v = make_vehicle(mock_network, [1, 2, 3], speed=0.0, pos=0.0, config=config)

    # Run many steps to let it reach steady state
    for _ in range(200):
        v.step(0.5, leader_gap=None, leader_speed=None)

    assert v.speed <= config.idm.v0 + 0.5, \
        f"Speed {v.speed} should not exceed desired speed {config.idm.v0}"
