"""Integration tests for the full simulation loop."""

import networkx as nx
from src.config import SimConfig
from src.network import RoadNetwork
from src.simulation import Simulation
from src.runner import SimulationRunner


def _build_test_network():
    """Create a small 4-node network with real networkx graph."""
    G = nx.MultiDiGraph()
    # Square: 1→2→3→4→1, with cross 1→3
    nodes = {
        1: {"x": -71.080, "y": 42.320},
        2: {"x": -71.079, "y": 42.320},
        3: {"x": -71.079, "y": 42.319},
        4: {"x": -71.080, "y": 42.319},
    }
    for nid, data in nodes.items():
        G.add_node(nid, **data, highway="residential")

    edges = [
        (1, 2, {"length": 80.0, "speed_kph": 50, "lanes": 2, "name": "A St", "travel_time": 5.76}),
        (2, 3, {"length": 100.0, "speed_kph": 50, "lanes": 1, "name": "B St", "travel_time": 7.2}),
        (3, 4, {"length": 80.0, "speed_kph": 50, "lanes": 2, "name": "C St", "travel_time": 5.76}),
        (4, 1, {"length": 100.0, "speed_kph": 50, "lanes": 1, "name": "D St", "travel_time": 7.2}),
        (1, 3, {"length": 120.0, "speed_kph": 40, "lanes": 1, "name": "Diagonal", "travel_time": 10.8}),
        (2, 1, {"length": 80.0, "speed_kph": 50, "lanes": 2, "name": "A St", "travel_time": 5.76}),
        (3, 2, {"length": 100.0, "speed_kph": 50, "lanes": 1, "name": "B St", "travel_time": 7.2}),
    ]
    for u, v, data in edges:
        G.add_edge(u, v, **data)

    signal_nodes = {1, 3}  # signals at nodes 1 and 3
    return RoadNetwork(G, signal_nodes)


def test_simulation_runs_without_crash():
    """Simulation should run 100 steps without raising."""
    network = _build_test_network()
    config = SimConfig()
    sim = Simulation(network, num_vehicles=10, config=config)

    for _ in range(100):
        sim.step()

    assert sim.time > 0
    assert len(sim.vehicles) > 0


def test_vehicle_count_stays_stable():
    """Vehicles that complete routes should be replaced by new spawns."""
    network = _build_test_network()
    config = SimConfig()
    sim = Simulation(network, num_vehicles=10, config=config)

    initial_count = len(sim.vehicles)

    for _ in range(200):
        sim.step()

    # Vehicle count should stay roughly stable (spawns replace dead)
    final_count = len(sim.vehicles)
    assert final_count >= initial_count * 0.5, \
        f"Vehicle count dropped too much: {initial_count} -> {final_count}"


def test_stats_are_recorded():
    """Stats should accumulate over simulation steps."""
    network = _build_test_network()
    sim = Simulation(network, num_vehicles=10)

    for _ in range(20):
        sim.step()

    assert len(sim.stats) > 0
    last = sim.stats[-1]
    assert "time" in last
    assert "active_vehicles" in last
    assert "avg_speed_kmh" in last
    assert last["avg_speed_kmh"] >= 0


def test_runner_create_and_reset():
    """SimulationRunner should create and reset simulations."""
    network = _build_test_network()
    runner = SimulationRunner(network)

    sim1 = runner.create(num_vehicles=5)
    assert sim1 is not None
    assert len(sim1.vehicles) == 5

    # Step a bit
    runner.step(10)
    assert sim1.time > 0

    # Reset
    sim2 = runner.reset(num_vehicles=8)
    assert sim2 is not sim1
    assert sim2.time == 0
    assert len(sim2.vehicles) == 8


def test_runner_export_trajectories(tmp_path):
    """SimulationRunner should export trajectories to CSV."""
    network = _build_test_network()
    runner = SimulationRunner(network)
    runner.create(num_vehicles=5)

    csv_path = str(tmp_path / "trajectories.csv")
    runner.export_trajectories(csv_path, duration=5.0, step_interval=2)

    import csv
    with open(csv_path) as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    assert "time" in header
    assert "vid" in header
    assert "speed_ms" in header
    assert len(rows) > 0, "Should have exported some trajectory data"


def test_different_configs_produce_different_behavior():
    """Changing IDM parameters should affect simulation outcome."""
    network = _build_test_network()

    # Default config
    config1 = SimConfig()
    sim1 = Simulation(network, num_vehicles=10, seed=42, config=config1)
    for _ in range(100):
        sim1.step()
    speed1 = sim1.stats[-1]["avg_speed_kmh"]

    # Clear segments for second run
    for seg in network.segments.values():
        seg.vehicles.clear()

    # Config with very low desired speed
    config2 = SimConfig()
    config2.idm.v0 = 5.0  # ~18 km/h instead of ~50 km/h
    sim2 = Simulation(network, num_vehicles=10, seed=42, config=config2)
    for _ in range(100):
        sim2.step()
    speed2 = sim2.stats[-1]["avg_speed_kmh"]

    assert speed2 < speed1, \
        f"Lower v0 should produce lower avg speed: {speed2} vs {speed1}"
