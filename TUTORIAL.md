# TrafficFlow Developer Tutorial

A microscopic traffic flow simulation for Boston, using the Intelligent Driver Model (IDM) and real OpenStreetMap road networks. This guide covers setup, architecture, development workflows, and integration points.

---

## Quick Start

```bash
# Clone and set up
git clone https://github.com/AmirGoldental/TrafficFlow.git
cd TrafficFlow
python -m venv venv
venv/Scripts/activate    # Windows
# source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt

# Run the web dashboard (data is included — no downloads needed)
python server.py

# Open http://127.0.0.1:8000 in your browser
```

The Warren St corridor data (road network graph + traffic signal locations) is included in `data/`. The server loads it from cache automatically.

### Other run modes

```bash
# Headless simulation with stats
python main.py --mode run --corridor warren_st --vehicles 200 --duration 300

# Export vehicle trajectories to CSV
python main.py --mode run --corridor warren_st --export data/trajectories.csv

# Use custom simulation parameters
python server.py --config my_config.json

# Run tests
pip install pytest
pytest tests/ -v
```

---

## Architecture Overview

```
server.py                  FastAPI WebSocket server (web dashboard)
main.py                    CLI entry point (map/animate/run modes)

src/
├── config.py              SimConfig — all tunable parameters
├── map_loader.py          Load road network + signals from OSM
├── network.py             RoadNetwork, RoadSegment, Intersection
├── simulation.py          Simulation engine (orchestrates one tick)
├── vehicle.py             Vehicle agent with IDM car-following
├── traffic_light.py       Signal controllers + clustering
├── follower.py            Leader-finding service (same-seg + spillback)
├── vehicle_tracker.py     Vehicle-to-segment mapping
├── runner.py              SimulationRunner — lifecycle manager
├── signal_controller.py   SignalController protocol (for extensions)
├── state_serializer.py    JSON serialization for WebSocket frames
└── visualizer.py          Matplotlib rendering (static map, animation)

web/
├── index.html             Dashboard HTML
├── style.css              Dark theme styling
└── js/app.js              MapLibre map + WebSocket client

tests/
├── conftest.py            Shared fixtures (MockNetwork, config)
├── test_vehicle.py        IDM behavior tests
├── test_traffic_light.py  Phase transition tests
├── test_follower.py       Leader-finding tests
└── test_simulation.py     Integration tests
```

### Data Flow

```
OSM (Overpass API)  →  map_loader.py  →  RoadNetwork  →  Simulation
Boston Signals API  →                                      ↓
                                                      SimulationRunner
                                                           ↓
                                              state_serializer.py → WebSocket → Browser
```

### Key Classes

| Class | File | Role |
|-------|------|------|
| `SimConfig` | config.py | All tunable parameters (IDM, vehicle, signal, sim) |
| `RoadNetwork` | network.py | Graph wrapper: nodes → `Intersection`, edges → `RoadSegment` |
| `Simulation` | simulation.py | One-tick orchestrator: lights → leaders → vehicles → cleanup |
| `Vehicle` | vehicle.py | IDM agent: acceleration, red-light stopping, segment transitions |
| `TrafficLight` | traffic_light.py | Two-phase controller with configurable timing |
| `TrafficLightManager` | traffic_light.py | Clusters signal nodes, manages all controllers |
| `FollowerService` | follower.py | Finds nearest leader (same-segment + cross-segment spillback) |
| `VehicleTracker` | vehicle_tracker.py | Owns vehicle↔segment mapping, builds per-step index |
| `SimulationRunner` | runner.py | Lifecycle: create, reset, step, export trajectories |

---

## Configuration

All simulation parameters are in `src/config.py` as nested dataclasses. You can override any of them via a JSON config file:

```json
{
  "dt": 0.5,
  "num_vehicles": 300,
  "seed": 42,
  "idm": {
    "v0": 13.9,
    "T": 1.5,
    "a": 1.5,
    "b": 2.0,
    "s0": 3.0,
    "delta": 4.0
  },
  "vehicle": {
    "length": 7.0,
    "width": 2.0,
    "stop_margin": 3.0,
    "lane_width": 3.5
  },
  "signal": {
    "green_duration": 30.0,
    "yellow_duration": 3.0,
    "cluster_radius_m": 60.0,
    "expand_seg_max_m": 20.0,
    "expand_dist_max_m": 40.0
  }
}
```

```bash
python server.py --config my_config.json
python main.py --mode run --config my_config.json
```

Only include the parameters you want to override — missing keys use defaults.

### IDM Parameters Reference

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `v0` | 13.9 m/s (50 km/h) | Desired speed |
| `T` | 1.5 s | Safe time headway |
| `a` | 1.5 m/s² | Max acceleration |
| `b` | 2.0 m/s² | Comfortable deceleration |
| `s0` | 3.0 m | Minimum gap (bumper-to-bumper) |
| `delta` | 4.0 | Acceleration exponent |

---

## Adding a New Corridor

Corridors are defined in `src/map_loader.py` in the `CORRIDORS` dict:

```python
CORRIDORS = {
    "warren_st": {
        "name": "Warren Street, Roxbury",
        "bbox": (42.332, 42.307, -71.074, -71.092),  # (north, south, east, west)
        "description": "Warren St from Blue Hill Ave to Dudley Sq, ~2.1 km",
    },
    # Add your corridor here:
    "your_corridor": {
        "name": "Your Corridor Name",
        "bbox": (north, south, east, west),
        "description": "Description of the corridor",
    },
}
```

### How to find the bounding box

1. Go to [bboxfinder.com](http://bboxfinder.com) or OpenStreetMap
2. Draw a rectangle around your area of interest
3. Note the coordinates: (north_lat, south_lat, east_lon, west_lon)
4. Add ~400m buffer (≈ 0.004°) on each side

### First run with a new corridor

```bash
# This will download the road network from OSM (requires internet)
python server.py --corridor your_corridor

# The graph will be cached in data/ for future runs
```

To include the new corridor's data in git, add its cache file to `.gitignore` exceptions:

```gitignore
!data/graph_bbox_<north>_<south>_<east>_<west>.pkl
```

---

## Integrating External Data

### Traffic signal data

The default signal source is the Boston Analyze Boston API + OSM tags. To use your own signal data:

1. **Option A: Provide a GeoJSON file** — Replace or supplement the signal loading in `map_loader.py`:

```python
def get_traffic_signal_nodes(G, custom_signals_path=None, ...):
    if custom_signals_path:
        with open(custom_signals_path) as f:
            signals_geojson = json.load(f)
    else:
        signals_geojson = download_boston_signals()
    # ... rest of matching logic
```

The GeoJSON should have Point features with lon/lat coordinates. Each signal is matched to the nearest graph node within 30m using a KDTree.

2. **Option B: Provide node IDs directly** — If you already know which OSM node IDs are signalized:

```python
signal_nodes = {12345, 67890, ...}  # set of OSM node IDs
network = RoadNetwork(G, signal_nodes)
```

### Speed limit data

Speed limits come from OSM via `osmnx.add_edge_speeds()`. If you have better data:

- Set the `speed_kph` attribute on graph edges before creating `RoadNetwork`
- Or modify `RoadNetwork._parse_speed()` in `network.py` to read your custom attribute

### Demand patterns (origin/destination)

Currently, vehicles are spawned with random O-D pairs. To use real demand data:

1. Override `Simulation._spawn_vehicle()` to sample from your demand distribution
2. Or subclass `Simulation` and inject custom O-D matrices

### Real-time signal timing

If you have real signal timing data (e.g., from a SCATS or SCOOT system):

1. Implement the `SignalController` protocol from `src/signal_controller.py`
2. Your controller needs: `step(dt)`, `is_green(edge_id)`, `get_state()`, `state`, `current_phase`
3. Inject it into `TrafficLightManager` in place of the default `TrafficLight`

---

## Extending the Simulation

### Adding a new car-following model

The IDM logic lives in `Vehicle._idm_accel()`. To add a different model:

1. Create a new method (e.g., `_krauss_accel()`) in `vehicle.py`
2. Add a `model` field to `IDMConfig` (or create a new config section)
3. Switch in `Vehicle.step()`:

```python
if self.config.idm.model == "krauss":
    self.accel = self._krauss_accel(v0, gap, v_lead)
else:
    self.accel = self._idm_accel(v0, gap, v_lead)
```

### Adding lane changing

Currently vehicles stay in their assigned lane. To add lane changing:

1. Add a `lane_change()` method to `Vehicle` that evaluates adjacent lanes
2. Use `VehicleTracker.get_sorted_vehicles()` to check gaps in the target lane
3. Call `tracker.move()` is not needed (same segment) — just update `v.lane`
4. Call lane changing logic from `Simulation.step()` after leader-finding

### Adding adaptive signal control

1. Implement `SignalController` protocol (see `src/signal_controller.py`)
2. Your controller receives queue info from `VehicleTracker.get_sorted_vehicles()`
3. Register it in `TrafficLightManager._build()` instead of creating `TrafficLight`

Example skeleton:

```python
class ActuatedController:
    def __init__(self, node_ids, incoming_segments, network):
        self.node_ids = node_ids
        self._phase_segs = [set(), set()]  # same structure as TrafficLight
        # ... classify segments into phases
        self._state = "green"
        self._current_phase = 0
        self._elapsed = 0.0
        self._min_green = 10.0
        self._max_green = 60.0

    def step(self, dt):
        self._elapsed += dt
        # Your adaptive logic here: extend green if vehicles are arriving,
        # switch if queue is empty, etc.

    def is_green(self, edge_id):
        if self._state == "yellow":
            return False
        return edge_id in self._phase_segs[self._current_phase]

    def get_state(self):
        return {"state": self._state, "phase": self._current_phase, ...}

    @property
    def state(self): return self._state

    @property
    def current_phase(self): return self._current_phase
```

### Adding data export / analysis

Use `SimulationRunner.export_trajectories()` for CSV export:

```python
from src.config import SimConfig
from src.map_loader import load_corridor
from src.network import RoadNetwork
from src.runner import SimulationRunner

G, signals = load_corridor("warren_st")
network = RoadNetwork(G, signals)

runner = SimulationRunner(network)
runner.create(num_vehicles=200)
runner.export_trajectories("output/trajectories.csv", duration=300.0)
```

The CSV contains: `time, vid, speed_ms, accel, pos, lane, route_idx, segment_u, segment_v, active`

---

## WebSocket Protocol

The server streams simulation state over WebSocket at `ws://localhost:8000/ws/simulation`.

### Messages from server

**`network`** (sent once on connect):
```json
{
  "type": "network",
  "roads": { "type": "FeatureCollection", "features": [...] },
  "signals": { "type": "FeatureCollection", "features": [...] },
  "indicators": { "type": "FeatureCollection", "features": [...] }
}
```

**`frame`** (sent every ~100ms):
```json
{
  "type": "frame",
  "time": 42.5,
  "vehicles": [[vid, lon, lat, bearing, speed, lane, num_lanes], ...],
  "signals": [{"node_id": 123, "edge_id": "1-2-0", "color": "green"}, ...],
  "stats": {"active_vehicles": 430, "avg_speed_kmh": 22.1}
}
```

**`inspect_result`** (on demand):
```json
{
  "type": "inspect_result",
  "target": "vehicle",
  "data": {"vid": 42, "speed_kmh": 35.2, "accel": 0.5, ...}
}
```

### Messages from client

```json
{"type": "control", "action": "pause"}
{"type": "control", "action": "play"}
{"type": "control", "action": "speed", "value": 2.0}
{"type": "control", "action": "reset"}
{"type": "inspect", "target": "vehicle", "id": 42}
{"type": "inspect", "target": "signal", "id": 12345}
```

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_vehicle.py -v

# Run with coverage (install pytest-cov first)
pytest tests/ --cov=src --cov-report=term-missing
```

### Writing new tests

Tests use a `MockNetwork` fixture (defined in `tests/conftest.py`) — a minimal 3-node network that doesn't require OSM data:

```python
def test_my_feature(mock_network, config):
    from src.vehicle import Vehicle

    v = Vehicle(vid=1, route=[1, 2, 3], network=mock_network,
                light_mgr=MockLightMgr(), config=config, speed=5.0)
    v.pos = 50.0
    v.step(0.5, leader_gap=None, leader_speed=None)
    assert v.speed > 5.0  # should accelerate in free flow
```

For integration tests that need a real networkx graph, see `tests/test_simulation.py` — it builds a small 4-node network with `nx.MultiDiGraph`.

---

## Project Structure Decisions

**Why IDM?** The Intelligent Driver Model is the standard in traffic research. It's simple (6 parameters), well-studied, and produces realistic behavior (acceleration, braking, traffic waves).

**Why not SUMO?** This project simulates from scratch to allow full control over the model, easy integration of custom data sources, and a lightweight web-based visualization without external dependencies.

**Why OSMnx?** It provides the road network as a networkx graph, which is the natural data structure for routing and simulation. It also handles speed limits, lane counts, and turn restrictions from OSM.

**Why MapLibre?** Free, no API key needed, supports GeoJSON layers with data-driven styling, and the CARTO dark basemap looks great for traffic visualization.

**Why WebSocket instead of REST?** The simulation streams 10 frames/second. Polling would be wasteful; WebSocket gives low-latency push with backpressure handling.
