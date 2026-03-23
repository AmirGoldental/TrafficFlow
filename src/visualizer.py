"""
Visualizer.

Two modes:
  - static_map(): plot the road network with traffic signal markers
  - animate():    live matplotlib animation of the simulation

Traffic light phases are shown as short coloured line segments on each
incoming edge: green = go, red = stop, yellow = clearing.
"""

from __future__ import annotations
import math
from typing import TYPE_CHECKING, List

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.collections import LineCollection
import numpy as np

if TYPE_CHECKING:
    from .simulation import Simulation
    from .network import RoadNetwork


# ------------------------------------------------------------------ helpers

def _lonlat_to_xy(lon, lat, origin_lon, origin_lat):
    """Approximate metres from an origin point (equirectangular)."""
    R = 6_371_000.0
    x = R * math.radians(lon - origin_lon) * math.cos(math.radians(origin_lat))
    y = R * math.radians(lat - origin_lat)
    return x, y


def _build_xy(network: "RoadNetwork"):
    """Precompute (x, y) in metres for every node."""
    nodes = list(network.intersections.values())
    ref_lon = sum(n.x for n in nodes) / len(nodes)
    ref_lat = sum(n.y for n in nodes) / len(nodes)

    xy = {}
    for n in nodes:
        xy[n.node_id] = _lonlat_to_xy(n.x, n.y, ref_lon, ref_lat)
    return xy


# Length of the directional phase indicator (metres in plot space)
INDICATOR_LEN = 25.0


def _build_signal_segments(network, xy):
    """
    Precompute the line segments used to draw phase indicators.
    Returns a list of dicts with keys:
        node_id, edge_id, x0, y0, x1, y1
    Each represents a short line near the intersection end of an incoming edge.
    """
    indicators = []
    for nid, inter in network.intersections.items():
        if not inter.is_signal or nid not in xy:
            continue
        nx_, ny_ = xy[nid]
        for seg in inter.incoming:
            if seg.u not in xy:
                continue
            ux, uy = xy[seg.u]
            # Direction from source to this node
            dx = nx_ - ux
            dy = ny_ - uy
            length = math.hypot(dx, dy)
            if length < 1.0:
                continue
            # Normalise
            dx /= length
            dy /= length
            # Indicator: from (node - INDICATOR_LEN * dir) to node
            ix0 = nx_ - INDICATOR_LEN * dx
            iy0 = ny_ - INDICATOR_LEN * dy
            indicators.append({
                "node_id": nid,
                "edge_id": seg.edge_id,
                "coords": ((ix0, iy0), (nx_, ny_)),
            })
    return indicators


# ------------------------------------------------------------------ static

def static_map(network: "RoadNetwork", save_path: str = None):
    """Draw the road network with traffic signals highlighted."""
    xy = _build_xy(network)

    fig, ax = plt.subplots(figsize=(14, 14))
    ax.set_facecolor("#1a1a2e")
    fig.patch.set_facecolor("#1a1a2e")

    # Draw edges
    for seg in network.segments.values():
        if seg.u not in xy or seg.v not in xy:
            continue
        x0, y0 = xy[seg.u]
        x1, y1 = xy[seg.v]
        ax.plot([x0, x1], [y0, y1], color="#3a3a5c", linewidth=0.4, alpha=0.6)

    # Draw signal nodes
    sig_x, sig_y = [], []
    for node_id, inter in network.intersections.items():
        if inter.is_signal and node_id in xy:
            sig_x.append(xy[node_id][0])
            sig_y.append(xy[node_id][1])

    ax.scatter(sig_x, sig_y, c="#f5a623", s=4, zorder=5,
               label=f"Traffic signals ({len(sig_x)})")

    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Boston Road Network — Traffic Signals",
                 color="white", fontsize=14)
    ax.legend(loc="lower right", framealpha=0.3, labelcolor="white")
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved static map to {save_path}")
    else:
        plt.show()
    plt.close(fig)


# ------------------------------------------------------------------ animation

def animate(sim: "Simulation", duration: float = 120.0, interval_ms: int = 100,
            save_path: str = None):
    """
    Animate the simulation.
    Traffic light phases are shown as short coloured line segments
    on each incoming edge (green/red/yellow).
    """
    network = sim.network
    xy = _build_xy(network)

    fig, ax = plt.subplots(figsize=(14, 14))
    ax.set_facecolor("#0d0d1a")
    fig.patch.set_facecolor("#0d0d1a")

    # Static road layer
    for seg in network.segments.values():
        if seg.u not in xy or seg.v not in xy:
            continue
        x0, y0 = xy[seg.u]
        x1, y1 = xy[seg.v]
        ax.plot([x0, x1], [y0, y1], color="#2a2a4a", linewidth=0.5, alpha=0.7)

    # ---- Phase indicator layer ----
    sig_indicators = _build_signal_segments(network, xy)
    # Build initial LineCollection
    seg_coords = [ind["coords"] for ind in sig_indicators]
    phase_lc = LineCollection(seg_coords, linewidths=2.5, zorder=5, alpha=0.9)
    phase_lc.set_color(["#f5a623"] * len(sig_indicators))
    ax.add_collection(phase_lc)

    # Vehicle scatter
    vehicle_scatter = ax.scatter([], [], s=6, c="cyan", zorder=6, alpha=0.8)

    ax.set_aspect("equal")
    ax.axis("off")
    time_text = ax.text(
        0.02, 0.97, "", transform=ax.transAxes,
        color="white", fontsize=10, va="top"
    )

    def _vehicle_positions():
        xs, ys, speeds = [], [], []
        for v in sim.vehicles.values():
            if not v.active:
                continue
            seg = v.current_segment
            if seg is None or seg.u not in xy or seg.v not in xy:
                continue
            x0, y0 = xy[seg.u]
            x1, y1 = xy[seg.v]
            t = min(v.pos / max(seg.length, 1), 1.0)
            xs.append(x0 + t * (x1 - x0))
            ys.append(y0 + t * (y1 - y0))
            speeds.append(v.speed)
        return xs, ys, speeds

    def _indicator_colors():
        colors = []
        for ind in sig_indicators:
            nid = ind["node_id"]
            eid = ind["edge_id"]
            light = sim.light_mgr.lights.get(nid)
            if light is None:
                colors.append("#f5a623")
            elif light.is_green(eid):
                colors.append("#00ff88")
            elif light.state == "yellow" and light.phase_for_edge(eid) == light.current_phase:
                colors.append("#ffdd00")
            else:
                colors.append("#ff3333")
        return colors

    total_frames = int(duration / sim.dt)

    def update(frame):
        sim.step()
        xs, ys, speeds = _vehicle_positions()
        if xs:
            vehicle_scatter.set_offsets(np.column_stack([xs, ys]))
            max_speed = 14.0
            normed = np.clip(np.array(speeds) / max_speed, 0, 1)
            colors = plt.cm.RdYlGn(normed)
            vehicle_scatter.set_color(colors)
        else:
            vehicle_scatter.set_offsets(np.zeros((0, 2)))

        # Update phase indicator colours
        if sig_indicators:
            phase_lc.set_color(_indicator_colors())

        s = sim.stats[-1] if sim.stats else {}
        time_text.set_text(
            f"t = {sim.time:.0f}s   vehicles = {s.get('active_vehicles', 0)}"
            f"   avg = {s.get('avg_speed_kmh', 0):.1f} km/h"
        )
        return vehicle_scatter, phase_lc, time_text

    ani = animation.FuncAnimation(
        fig, update, frames=total_frames, interval=interval_ms, blit=True
    )

    if save_path:
        print(f"Saving animation to {save_path} (this may take a while)...")
        ani.save(save_path, writer="pillow", fps=10)
        print("Done.")
    else:
        plt.show()
    plt.close(fig)
