"""
Entry point for the Boston traffic flow simulation.

Usage:
    python main.py [--mode map|animate|run] [--corridor warren_st] [--vehicles N] [--duration S]

Modes:
    map       — render a static map of the network + signals
    animate   — run simulation with live matplotlib animation
    run       — run headless simulation and print stats
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.map_loader import load_graph, load_corridor, get_traffic_signal_nodes, CORRIDORS
from src.network import RoadNetwork
from src.simulation import Simulation
from src.visualizer import static_map, animate


def main():
    parser = argparse.ArgumentParser(description="Boston Traffic Flow Simulation")
    parser.add_argument("--mode", choices=["map", "animate", "run"], default="animate")
    parser.add_argument("--corridor", choices=list(CORRIDORS.keys()),
                        default=None, help="Focus on a specific corridor")
    parser.add_argument("--vehicles", type=int, default=None,
                        help="Number of vehicles (default: auto-scaled)")
    parser.add_argument("--duration", type=float, default=300.0,
                        help="Simulation duration (s)")
    parser.add_argument("--save", type=str, default=None,
                        help="Save output to file (PNG for map, GIF for animate)")
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()

    # ---------------------------------------------------------------- data
    if args.corridor:
        G, signal_nodes = load_corridor(args.corridor,
                                        force_download=args.force_download)
    else:
        print("=== Loading Boston city-wide road network ===")
        G = load_graph(force_download=args.force_download)
        signal_nodes = get_traffic_signal_nodes(G)

    network = RoadNetwork(G, signal_nodes)

    # Auto-scale vehicles: ~1 per 3 road segments for corridors, fewer city-wide
    if args.vehicles is not None:
        num_vehicles = args.vehicles
    elif args.corridor:
        num_vehicles = max(50, len(network.segments) // 3)
    else:
        num_vehicles = 300

    # ---------------------------------------------------------------- mode
    if args.mode == "map":
        print(f"\n=== Rendering static map ===")
        save = args.save or f"data/{'corridor_' + args.corridor if args.corridor else 'boston'}_map.png"
        static_map(network, save_path=save)

    elif args.mode == "animate":
        print(f"\n=== Simulation: {num_vehicles} vehicles, {args.duration}s ===")
        sim = Simulation(network, num_vehicles=num_vehicles)
        animate(sim, duration=args.duration, save_path=args.save)

    elif args.mode == "run":
        print(f"\n=== Headless simulation: {num_vehicles} vehicles, {args.duration}s ===")
        sim = Simulation(network, num_vehicles=num_vehicles)
        sim.run(duration=args.duration)

        if sim.stats:
            avg_speeds = [s["avg_speed_kmh"] for s in sim.stats]
            print(f"\nResults:")
            print(f"  Duration simulated : {sim.time:.1f} s")
            print(f"  Vehicles           : {num_vehicles}")
            print(f"  Mean speed         : {sum(avg_speeds)/len(avg_speeds):.2f} km/h")
            print(f"  Min avg speed      : {min(avg_speeds):.2f} km/h")
            print(f"  Max avg speed      : {max(avg_speeds):.2f} km/h")


if __name__ == "__main__":
    main()
