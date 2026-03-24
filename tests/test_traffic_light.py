"""Tests for TrafficLight phase transitions."""

from src.config import SimConfig, SignalConfig
from src.traffic_light import TrafficLight


def make_light(network, signal_config=None):
    """Create a traffic light for node 2 in mock network."""
    sc = signal_config or SignalConfig()
    incoming = list(network.intersections[2].incoming)
    return TrafficLight(
        node_ids=[2], incoming_segments=incoming,
        network=network, offset=0.0, signal_config=sc,
    )


def test_initial_state_is_green(mock_network):
    light = make_light(mock_network)
    assert light.state == "green"
    assert light.current_phase == 0


def test_green_to_yellow_transition(mock_network):
    sc = SignalConfig(green_duration=30.0, yellow_duration=3.0)
    light = make_light(mock_network, sc)

    # Step to just past green duration
    for _ in range(61):  # 61 * 0.5 = 30.5s
        light.step(0.5)

    assert light.state == "yellow", f"Expected yellow, got {light.state}"


def test_yellow_to_green_switches_phase(mock_network):
    sc = SignalConfig(green_duration=30.0, yellow_duration=3.0)
    light = make_light(mock_network, sc)

    initial_phase = light.current_phase

    # Step through one full green + yellow cycle
    for _ in range(67):  # 67 * 0.5 = 33.5s > 30 + 3 = 33
        light.step(0.5)

    assert light.state == "green"
    assert light.current_phase != initial_phase, "Phase should have switched"


def test_is_green_for_active_phase(mock_network):
    light = make_light(mock_network)
    seg = list(mock_network.intersections[2].incoming)[0]

    # Edge should be in one of the phases
    phase = light.phase_for_edge(seg.edge_id)
    assert phase >= 0, "Edge should be assigned to a phase"

    # If it's in the current phase and state is green, is_green should be True
    if phase == light.current_phase and light.state == "green":
        assert light.is_green(seg.edge_id)


def test_offset_affects_initial_state(mock_network):
    sc = SignalConfig(green_duration=30.0, yellow_duration=3.0)
    # Offset of 31s should start in yellow
    light = TrafficLight(
        node_ids=[2],
        incoming_segments=list(mock_network.intersections[2].incoming),
        network=mock_network,
        offset=31.0,
        signal_config=sc,
    )
    assert light.state == "yellow", f"Offset 31s should start in yellow, got {light.state}"


def test_time_until_green_when_red(mock_network):
    sc = SignalConfig(green_duration=30.0, yellow_duration=3.0)
    light = make_light(mock_network, sc)

    # Find an edge in the non-active phase
    for phase_idx, edge_set in enumerate(light._phase_segs):
        if phase_idx != light.current_phase and edge_set:
            edge_id = next(iter(edge_set))
            t = light.time_until_green(edge_id)
            assert t > 0, "Time until green should be positive for red edge"
            break
