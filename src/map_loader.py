"""
Load road network from OpenStreetMap and traffic signal locations.
Supports city-wide or corridor-scoped loading.

Traffic signal sources:
  1. City of Boston / Analyze Boston (837 signals) — primary
  2. OSM node tags — fallback
"""

import os
import json
import pickle
import math
import osmnx as ox
import numpy as np
import requests
from scipy.spatial import cKDTree

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
GRAPH_CACHE = os.path.join(DATA_DIR, "boston_graph.pkl")
SIGNALS_CACHE = os.path.join(DATA_DIR, "boston_signals.geojson")

PLACE = "Boston, Massachusetts, USA"

# Analyze Boston ArcGIS REST endpoint for traffic signals
BOSTON_SIGNALS_URL = (
    "https://gisportal.boston.gov/arcgis/rest/services/"
    "Infrastructure/OpenData/MapServer/12/query"
)


# ------------------------------------------------------------------ graph

def load_graph(place: str = PLACE, force_download: bool = False,
               bbox: tuple = None):
    """
    Return a MultiDiGraph of the drivable road network.

    Args:
        place: place name for city-wide download
        bbox: (north, south, east, west) to download a bounding box instead
        force_download: re-download even if cached
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    cache = os.path.join(DATA_DIR, _cache_key(place, bbox) + ".pkl")

    if not force_download and os.path.exists(cache):
        print("Loading graph from cache...")
        with open(cache, "rb") as f:
            G = pickle.load(f)
        print(f"  Loaded: {len(G.nodes)} nodes, {len(G.edges)} edges")
        return G

    if bbox:
        north, south, east, west = bbox
        print(f"Downloading road network for bbox {bbox} ...")
        # osmnx v2 expects bbox as (left, bottom, right, top) = (west, south, east, north)
        G = ox.graph_from_bbox(bbox=(west, south, east, north),
                               network_type="drive", retain_all=False)
    else:
        print(f"Downloading road network for {place} ...")
        G = ox.graph_from_place(place, network_type="drive", retain_all=False)

    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)

    with open(cache, "wb") as f:
        pickle.dump(G, f)

    print(f"  Downloaded: {len(G.nodes)} nodes, {len(G.edges)} edges")
    return G


def _cache_key(place, bbox):
    if bbox:
        return f"graph_bbox_{bbox[0]:.4f}_{bbox[1]:.4f}_{bbox[2]:.4f}_{bbox[3]:.4f}"
    return "boston_graph"


# ------------------------------------------------------------------ signals

def download_boston_signals(force: bool = False) -> dict:
    """Download all 837 traffic signals from Analyze Boston."""
    os.makedirs(DATA_DIR, exist_ok=True)

    if not force and os.path.exists(SIGNALS_CACHE):
        with open(SIGNALS_CACHE) as f:
            return json.load(f)

    print("Downloading traffic signals from Analyze Boston ...")
    params = {"where": "1=1", "outFields": "*", "f": "geojson",
              "resultRecordCount": 2000}
    r = requests.get(BOSTON_SIGNALS_URL, params=params)
    r.raise_for_status()
    data = r.json()

    with open(SIGNALS_CACHE, "w") as f:
        json.dump(data, f)

    print(f"  Downloaded {len(data['features'])} signals")
    return data


def get_traffic_signal_nodes(G, search_radius_m: float = 30.0,
                             bbox: tuple = None) -> set:
    """
    Match Analyze Boston signal coordinates to the nearest graph node.

    Uses a KDTree for O(n log n) spatial lookup instead of brute-force.
    If `bbox` is provided as (north, south, east, west), only signals
    inside that bbox (with a small buffer) are considered.

    Falls back to OSM highway=traffic_signals tags for any remaining.
    """
    signals_geojson = download_boston_signals()
    features = signals_geojson.get("features", [])

    # Filter signals to bbox if provided (with 200m ≈ 0.002° buffer)
    if bbox:
        north, south, east, west = bbox
        buf = 0.002
        features = [
            f for f in features
            if (f["geometry"]["coordinates"]
                and south - buf <= f["geometry"]["coordinates"][1] <= north + buf
                and west - buf <= f["geometry"]["coordinates"][0] <= east + buf)
        ]
        print(f"  Signals in corridor bbox: {len(features)}")

    # Build KDTree from graph nodes for fast nearest-neighbor lookup
    node_list = list(G.nodes(data=True))
    node_ids = [nid for nid, _ in node_list]
    node_coords = np.array([[data["y"], data["x"]] for _, data in node_list])
    tree = cKDTree(node_coords)

    # Convert search radius from metres to approximate degrees
    # 1° lat ≈ 111,000 m; 1° lon ≈ 111,000 * cos(lat) m
    avg_lat = node_coords[:, 0].mean()
    radius_deg = search_radius_m / 111_000.0  # conservative (lat direction)

    matched = set()
    for feat in features:
        coords = feat["geometry"]["coordinates"]
        lon, lat = coords[0], coords[1]
        dist, idx = tree.query([lat, lon])
        if dist <= radius_deg:
            matched.add(node_ids[idx])

    # Fallback: also include OSM-tagged signal nodes
    osm_signals = {
        nid for nid, data in G.nodes(data=True)
        if data.get("highway") == "traffic_signals"
    }
    combined = matched | osm_signals

    print(f"  Traffic signals: {len(matched)} from Analyze Boston, "
          f"{len(osm_signals)} from OSM, {len(combined)} combined (deduplicated)")
    return combined


def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Distance in metres between two lat/lon points."""
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ------------------------------------------------------------------ corridor

# Pre-defined corridors
CORRIDORS = {
    "warren_st": {
        "name": "Warren Street, Roxbury",
        # bbox: (north, south, east, west) — tighter corridor with ~400m buffer
        # Warren St runs NW-SE from Dudley Sq (42.3275, -71.0775) to
        # Grove Hall / Blue Hill Ave (42.3095, -71.0835)
        "bbox": (42.332, 42.307, -71.074, -71.092),
        "description": "Warren St from Blue Hill Ave (Grove Hall) to Dudley Sq, ~2.1 km",
    },
    "mass_ave": {
        "name": "Massachusetts Avenue",
        "bbox": (42.355, 42.330, -71.075, -71.105),
        "description": "Mass Ave from Beacon St to Columbia Rd",
    },
    "comm_ave": {
        "name": "Commonwealth Avenue",
        "bbox": (42.355, 42.345, -71.095, -71.140),
        "description": "Comm Ave from Kenmore to BU Bridge",
    },
}


def load_corridor(corridor_name: str, force_download: bool = False):
    """
    Load a corridor by name. Returns (graph, signal_nodes).
    """
    corridor = CORRIDORS[corridor_name]
    print(f"=== Corridor: {corridor['name']} ===")
    print(f"    {corridor['description']}")

    G = load_graph(bbox=corridor["bbox"], force_download=force_download)
    signal_nodes = get_traffic_signal_nodes(G, bbox=corridor["bbox"])
    return G, signal_nodes


if __name__ == "__main__":
    G, signals = load_corridor("warren_st")
    print(f"Sample signal nodes: {list(signals)[:5]}")
