"""
Serialize simulation state to JSON-friendly dicts for the web dashboard.

Three outputs:
  - network (static, sent once): roads + signal positions as GeoJSON
  - frame  (per tick): vehicle positions + signal states + stats
  - inspect (on demand): detailed info about a vehicle or signal
"""

from __future__ import annotations
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .simulation import Simulation
    from .network import RoadNetwork

# ------------------------------------------------------------------ constants
VEHICLE_LENGTH = 7.0   # metres
VEHICLE_WIDTH = 2.0    # metres
LANE_WIDTH = 3.5       # metres

# At Boston latitude (~42.32°)
DEG_LAT = 111_320.0                              # metres per degree latitude
DEG_LON = 111_320.0 * math.cos(math.radians(42.32))  # ~82,500 m per degree lon


def _arrow_polygon(center_lon, center_lat, bearing_rad, length_m, width_m, lane, num_lanes):
    """
    Build 5-vertex arrow polygon |=> in lon/lat coordinates.

    bearing_rad: direction of travel (radians, 0=east, pi/2=north)
    lane: 0-indexed lane from right
    num_lanes: total lanes on the segment
    """
    half_l = length_m / 2
    half_w = width_m / 2
    nose = length_m * 0.3   # how far the nose extends beyond the rectangle

    # Unit vectors along and perpendicular to bearing
    dx = math.cos(bearing_rad)
    dy = math.sin(bearing_rad)
    # Perpendicular (left of travel direction)
    px = -dy
    py = dx

    # Lane offset: center lanes around the road centerline
    # Lane 0 = rightmost, lane N-1 = leftmost
    lane_offset = (lane - (num_lanes - 1) / 2) * LANE_WIDTH

    # Apply lane offset to center position
    cx = center_lon + (lane_offset * px) / DEG_LON
    cy = center_lat + (lane_offset * py) / DEG_LAT

    # 5 vertices of |=> shape (back-left, front-left, nose, front-right, back-right)
    def pt(along, perp):
        """Convert local (along, perp) offset in metres to (lon, lat)."""
        return [
            round(cx + (along * dx + perp * px) / DEG_LON, 7),
            round(cy + (along * dy + perp * py) / DEG_LAT, 7),
        ]

    return [
        pt(-half_l, -half_w),       # back-right
        pt(-half_l,  half_w),       # back-left
        pt(half_l - nose,  half_w), # front-left (rectangle edge)
        pt(half_l + nose,  0),      # nose tip
        pt(half_l - nose, -half_w), # front-right (rectangle edge)
        pt(-half_l, -half_w),       # close polygon
    ]


def serialize_network(network: "RoadNetwork") -> dict:
    """Static road geometry + signal positions. Sent once on connect."""
    road_features = []
    for seg in network.segments.values():
        u = network.intersections[seg.u]
        v = network.intersections[seg.v]
        road_features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[u.x, u.y], [v.x, v.y]],
            },
            "properties": {
                "name": seg.name,
                "speed_limit_kmh": round(seg.speed_limit * 3.6, 1),
                "lanes": seg.lanes,
                "edge_id": f"{seg.u}-{seg.v}-{seg.edge_id[2]}",
            },
        })

    signal_features = []
    for nid, inter in network.intersections.items():
        if inter.is_signal:
            signal_features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [inter.x, inter.y]},
                "properties": {"node_id": nid},
            })

    # Signal directional indicators — short lines on each incoming edge
    indicator_features = []
    for nid, inter in network.intersections.items():
        if not inter.is_signal:
            continue
        for seg in inter.incoming:
            u_inter = network.intersections[seg.u]
            # Short line from approach direction into the intersection
            dx = inter.x - u_inter.x  # degrees lon
            dy = inter.y - u_inter.y  # degrees lat
            # Convert to metres using proper per-axis scaling
            dx_m = dx * DEG_LON
            dy_m = dy * DEG_LAT
            length_m = math.hypot(dx_m, dy_m)
            if length_m < 1.0:
                continue
            # ~25m indicator
            frac = min(0.3, 25.0 / length_m)
            start_lon = inter.x - frac * dx
            start_lat = inter.y - frac * dy
            indicator_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [start_lon, start_lat],
                        [inter.x, inter.y],
                    ],
                },
                "properties": {
                    "node_id": nid,
                    "edge_id": f"{seg.u}-{seg.v}-{seg.edge_id[2]}",
                },
            })

    return {
        "roads": {"type": "FeatureCollection", "features": road_features},
        "signals": {"type": "FeatureCollection", "features": signal_features},
        "indicators": {"type": "FeatureCollection", "features": indicator_features},
    }


def serialize_frame(sim: "Simulation") -> dict:
    """Per-tick state: vehicle positions, signal states, stats."""
    vehicles = _serialize_vehicles(sim)
    signals = _serialize_signals(sim)
    stats = sim.stats[-1] if sim.stats else {}

    return {
        "type": "frame",
        "time": round(sim.time, 1),
        "vehicles": vehicles,
        "signals": signals,
        "stats": {
            "active_vehicles": stats.get("active_vehicles", 0),
            "avg_speed_kmh": round(stats.get("avg_speed_kmh", 0), 1),
        },
    }


def _serialize_vehicles(sim: "Simulation") -> dict:
    features = []
    for v in sim.vehicles.values():
        if not v.active:
            continue
        seg = v.current_segment
        if seg is None:
            continue
        u = sim.network.intersections[seg.u]
        vi = sim.network.intersections[seg.v]
        t = min(v.pos / max(seg.length, 1.0), 1.0)
        lon = u.x + t * (vi.x - u.x)
        lat = u.y + t * (vi.y - u.y)

        # Bearing from u to v (radians, 0=east, pi/2=north)
        bearing = math.atan2(vi.y - u.y, vi.x - u.x)

        poly = _arrow_polygon(
            lon, lat, bearing,
            VEHICLE_LENGTH, VEHICLE_WIDTH,
            v.lane, seg.lanes,
        )

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [poly],
            },
            "properties": {
                "vid": v.vid,
                "speed": round(v.speed, 2),
                "speed_kmh": round(v.speed * 3.6, 1),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _serialize_signals(sim: "Simulation") -> list:
    """Compact signal state: list of {node_id, edge_id, green} per indicator."""
    result = []
    for nid, inter in sim.network.intersections.items():
        if not inter.is_signal:
            continue
        light = sim.light_mgr.lights.get(nid)
        if light is None:
            continue
        for seg in inter.incoming:
            eid = f"{seg.u}-{seg.v}-{seg.edge_id[2]}"
            if light.state == "yellow":
                # Only the phase that was green shows yellow; others stay red
                edge_phase = light.phase_for_edge(seg.edge_id)
                color = "yellow" if edge_phase == light.current_phase else "red"
            elif light.is_green(seg.edge_id):
                color = "green"
            else:
                color = "red"
            result.append({
                "node_id": nid,
                "edge_id": eid,
                "color": color,
            })
    return result


def serialize_vehicle_detail(sim: "Simulation", vid: int) -> dict:
    """Detailed info about a specific vehicle."""
    v = sim.vehicles.get(vid)
    if v is None:
        return {"error": f"Vehicle {vid} not found"}
    seg = v.current_segment
    return {
        "vid": v.vid,
        "speed_ms": round(v.speed, 2),
        "speed_kmh": round(v.speed * 3.6, 1),
        "accel": round(v.accel, 2),
        "active": v.active,
        "route_progress": f"{v.route_idx + 1}/{len(v.route)}",
        "distance_total_m": round(v.distance_total, 1),
        "current_road": seg.name if seg else "",
        "segment_pos": f"{v.pos:.1f}/{seg.length:.1f} m" if seg else "",
    }


def serialize_signal_detail(sim: "Simulation", node_id: int) -> dict:
    """Detailed info about a specific traffic signal."""
    light = sim.light_mgr.lights.get(node_id)
    if light is None:
        return {"error": f"Signal {node_id} not found"}
    inter = sim.network.intersections.get(node_id)
    return {
        "node_id": node_id,
        "controller_nodes": light.node_ids,
        "current_phase": light.current_phase,
        "state": light.state,
        "phase_0_edges": len(light._phase_segs[0]),
        "phase_1_edges": len(light._phase_segs[1]),
        "incoming_roads": list({
            s.name for s in inter.incoming if s.name
        }) if inter else [],
    }
