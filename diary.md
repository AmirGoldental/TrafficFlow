# TrafficFlow Development Diary

## 2026-03-23 — Session 1: Project Bootstrap

### Map Loading & Data
- Set up Python project with venv, osmnx, networkx, matplotlib
- Built `src/map_loader.py`: loads Boston road network from OSM via osmnx, caches to disk
- Integrated traffic signal data from two sources:
  - Analyze Boston open data (CSV with lat/lon)
  - OSM `highway=traffic_signals` nodes
  - Deduplicated using scipy KDTree (30m radius)
- Defined corridor system in `CORRIDORS` dict — started with full Boston, then narrowed

### Simulation Engine
- `src/network.py`: RoadNetwork wrapping osmnx MultiDiGraph into RoadSegment + Intersection dataclasses
- `src/vehicle.py`: IDM (Intelligent Driver Model) for longitudinal car-following
- `src/simulation.py`: Main loop — per-tick: step lights, find leaders, step vehicles, collect stats
- `src/traffic_light.py`: Two-phase N-S/E-W signal controller with bearing-based phase assignment
  - Added signal clustering (40m radius) so nearby OSM nodes share one controller

### Corridor Focus
- Started with full Boston — bbox too large for Overpass, signal coverage poor
- Narrowed to Warren St corridor (Roxbury): ~2.1 km, 612 nodes, 1290 edges, 52 signals
- Generated GIF animation with matplotlib

### Matplotlib Visualizer
- `src/visualizer.py`: dark-themed matplotlib animation
- Directional signal indicators (short colored lines per approach)
- Vehicle dots colored by speed
- Saved as GIF

---

## 2026-03-23 — Session 2: Web Dashboard

### Architecture Decision
- Replaced matplotlib with interactive web dashboard (Option A from proposal)
- Stack: FastAPI + WebSocket backend, MapLibre GL JS frontend (free, no API key)
- Used a Plan agent to design file structure, WebSocket protocol, layer strategy

### Backend (`server.py`)
- FastAPI with lifespan event for graph loading
- WebSocket endpoint streams simulation state at 20fps
- Control messages: play/pause/speed/reset
- Inspect messages: click vehicle or signal for details

### Serialization (`src/state_serializer.py`)
- `serialize_network()`: static road GeoJSON + signal indicators (sent once)
- `serialize_frame()`: per-tick vehicle positions + signal states + stats
- Vehicle positions interpolated from segment u→v nodes

### Frontend (`web/`)
- `index.html`: MapLibre canvas + controls bar + stats sidebar
- `js/app.js`: WebSocket connection, layer management, click handlers, sparkline
- `style.css`: Dark theme matching the simulation aesthetic
- CARTO dark basemap tiles (no API key needed)
- Data-driven styling: vehicle color by speed, signal indicators by phase

### Vehicle Visualization Upgrade
- Changed from circle dots to 5-sided arrow polygons (`|=>`)
- 7m long, 2m wide, computed as lon/lat polygons per frame
- Lane assignment: vehicles pick a lane, offset laterally by 3.5m per lane
- Leaders matched per-lane so different lanes are independent
- 3m minimum bumper-to-bumper gap (IDM s0)

### Bug Review (Review Agent Loop 1)
Ran a dedicated reviewer agent — found 10 bugs:

**Critical (fixed):**
1. Leader-finding sort order was backwards (found farthest vehicle, not nearest) — IDM was fundamentally broken
2. Shared `_network` not cleared on reset — stale vehicle IDs corrupted segment lists

**Medium (fixed):**
3. Hard clamp used stale leader position after vehicle transitioned to new segment
4. Frontend accessed MapLibre private API (`src._data`) for signal color updates
5. WebSocket reconnection created duplicate MapLibre layers/sources
6. `list.remove()` could crash on corrupted segment vehicle lists

**Low (fixed):**
7. Stats list grew unbounded (memory leak) — capped at 500
8. All signal edges showed yellow during clearance (should only be previously-green phase)
9. Indicator length math used approximate single-factor degree conversion
10. Missing `travel_time` edge attribute could crash pathfinding — added KeyError catch
