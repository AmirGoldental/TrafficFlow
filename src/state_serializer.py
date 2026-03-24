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


def serialize_network(network: "RoadNetwork", light_mgr=None) -> dict:
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

    # Signal directional indicators — per controller, positioned at cluster centroid
    indicator_features = []
    if light_mgr is not None:
        seen_controllers = set()
        for ctrl in light_mgr._controllers:
            ctrl_id = id(ctrl)
            if ctrl_id in seen_controllers:
                continue
            seen_controllers.add(ctrl_id)

            # Cluster centroid
            cx = sum(network.intersections[nid].x for nid in ctrl.node_ids) / len(ctrl.node_ids)
            cy = sum(network.intersections[nid].y for nid in ctrl.node_ids) / len(ctrl.node_ids)
            cluster_set = set(ctrl.node_ids)
            representative_nid = ctrl.node_ids[0]

            # Collect unique incoming segments, skipping intra-cluster
            seen_edges = set()
            for nid in ctrl.node_ids:
                inter = network.intersections[nid]
                for seg in inter.incoming:
                    if seg.edge_id in seen_edges:
                        continue
                    if seg.u in cluster_set:
                        continue  # intra-cluster segment
                    seen_edges.add(seg.edge_id)

                    u_inter = network.intersections[seg.u]
                    dx = cx - u_inter.x
                    dy = cy - u_inter.y
                    dx_m = dx * DEG_LON
                    dy_m = dy * DEG_LAT
                    length_m = math.hypot(dx_m, dy_m)
                    if length_m < 1.0:
                        continue
                    frac = min(0.3, 25.0 / length_m)
                    start_lon = cx - frac * dx
                    start_lat = cy - frac * dy
                    indicator_features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [start_lon, start_lat],
                                [cx, cy],
                            ],
                        },
                        "properties": {
                            "node_id": representative_nid,
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


def _serialize_vehicles(sim: "Simulation") -> list:
    """Compact vehicle list: [vid, lon, lat, bearing, speed, lane, lanes] per vehicle."""
    result = []
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
        bearing = math.atan2(vi.y - u.y, vi.x - u.x)

        result.append([
            v.vid,
            round(lon, 6),
            round(lat, 6),
            round(bearing, 3),
            round(v.speed, 2),
            v.lane,
            seg.lanes,
        ])
    return result


def _serialize_signals(sim: "Simulation") -> list:
    """Compact signal state: list of {node_id, edge_id, color} per indicator."""
    result = []
    seen_controllers = set()
    for ctrl in sim.light_mgr._controllers:
        ctrl_id = id(ctrl)
        if ctrl_id in seen_controllers:
            continue
        seen_controllers.add(ctrl_id)

        representative_nid = ctrl.node_ids[0]
        cluster_set = set(ctrl.node_ids)
        seen_edges = set()

        for nid in ctrl.node_ids:
            inter = sim.network.intersections[nid]
            for seg in inter.incoming:
                if seg.edge_id in seen_edges:
                    continue
                if seg.u in cluster_set:
                    continue  # skip intra-cluster segments
                seen_edges.add(seg.edge_id)

                eid = f"{seg.u}-{seg.v}-{seg.edge_id[2]}"
                if ctrl.state == "yellow":
                    edge_phase = ctrl.phase_for_edge(seg.edge_id)
                    color = "yellow" if edge_phase == ctrl.current_phase else "red"
                elif ctrl.is_green(seg.edge_id):
                    color = "green"
                else:
                    color = "red"
                result.append({
                    "node_id": representative_nid,
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
    from .traffic_light import GREEN_DURATION, YELLOW_DURATION, CYCLE_LENGTH

    light = sim.light_mgr.lights.get(node_id)
    if light is None:
        return {"error": f"Signal {node_id} not found"}
    inter = sim.network.intersections.get(node_id)

    # Build phase group info with road names and current state
    phase_groups = []
    for phase_idx, edge_set in enumerate(light._phase_segs):
        roads = set()
        for edge_id in edge_set:
            seg = sim.network.segments.get(edge_id)
            if seg and seg.name:
                roads.add(seg.name)

        if light.state == "yellow":
            phase_state = "yellow" if phase_idx == light.current_phase else "red"
        elif phase_idx == light.current_phase:
            phase_state = "green"
        else:
            phase_state = "red"

        phase_label = "N-S" if phase_idx == 0 else "E-W"
        phase_groups.append({
            "phase": phase_idx,
            "label": phase_label,
            "state": phase_state,
            "roads": sorted(roads) if roads else ["(unnamed)"],
        })

    if light.state == "green":
        time_remaining = round(GREEN_DURATION - light._elapsed, 1)
    else:
        time_remaining = round(YELLOW_DURATION - light._elapsed, 1)

    return {
        "node_id": node_id,
        "controller_nodes": light.node_ids,
        "current_phase": light.current_phase,
        "state": light.state,
        "phase_groups": phase_groups,
        "green_duration": GREEN_DURATION,
        "yellow_duration": YELLOW_DURATION,
        "cycle_length": CYCLE_LENGTH,
        "time_remaining": max(0, time_remaining),
        "incoming_roads": list({
            s.name for s in inter.incoming if s.name
        }) if inter else [],
    }
