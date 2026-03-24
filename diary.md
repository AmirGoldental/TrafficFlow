# TrafficFlow Development Diary

## 2026-03-23 — Session 1: Project Bootstrap

Amir asked me to build a traffic flow simulation for Boston from scratch — no existing simulators. He wanted to start with the map, find traffic lights, and simulate flow.

I decided to use osmnx to pull the road network from OpenStreetMap, since it gives us a graph we can simulate on directly. For traffic signals, I pulled from two sources — Analyze Boston's open data (a CSV with 848 signals) and OSM's own `highway=traffic_signals` tags. I used scipy's KDTree to deduplicate them within 30m, which gave us better coverage than either source alone.

For the simulation itself, I chose the Intelligent Driver Model (IDM) — it's the standard microscopic car-following model in traffic research and only needs a few parameters. Each vehicle follows a pre-computed shortest path and reacts to the vehicle ahead and to red lights.

I started with the full city of Boston, but the bbox was too large for Overpass API and the signal coverage was sparse. Amir suggested focusing on a specific corridor. We initially looked at a few options and he picked **Warren St** in Roxbury — about 2.1 km, 612 nodes, 1290 edges, 52 traffic signals. Dense enough to be interesting.

Built a matplotlib visualizer with a dark theme, directional signal indicators, and speed-colored vehicle dots. Generated a GIF to verify everything worked. It did, but Amir noted three problems:
1. Vehicles were stacking on top of each other at red lights
2. Couldn't tell which phase was green from the visualization
3. Some intersections had multiple signal nodes that should act as one

I fixed the signal clustering (40m radius merging nearby nodes into one controller), added directional phase indicators, and improved the vehicle gap handling.

---

## 2026-03-23 — Session 2: Web Dashboard & Vehicle Polygons

Amir wanted better visualization and interactivity. I proposed three options: enhanced matplotlib, a full web dashboard, or a hybrid. He went with the web dashboard.

I used a **Plan agent** to design the architecture before writing any code — it read all the source files and produced a detailed plan for file structure, WebSocket protocol, and MapLibre layer strategy. This was valuable because it let me write all the files without stopping to re-read code mid-implementation.

**Stack decision:** FastAPI + WebSocket for the backend (streams simulation state at 20fps), MapLibre GL JS for the frontend (free, no API key needed — I used CARTO dark basemap tiles). The simulation runs server-side; the browser just renders.

The frontend has three zones: the map canvas, a controls bar (play/pause, speed slider, reset), and a stats sidebar (vehicle count, avg speed, speed history sparkline). You can click on vehicles or signal indicators to inspect them.

Then Amir asked for proper vehicle shapes: 7m long, 5-sided arrow polygons (`|=>`), with 3m bumper-to-bumper gaps and lane separation on multi-lane roads. This meant:
- Computing arrow polygon vertices in lon/lat coordinates per frame (using bearing from segment direction)
- Adding lane assignment to vehicles (lateral offset by 3.5m per lane)
- Matching leaders per-lane so cars in different lanes don't block each other
- Switching the frontend from circle markers to fill polygons

### The Reviewer Agent Experiment

After getting everything working visually, I ran a **dedicated reviewer agent** to find bugs. This turned out to be one of the most impactful decisions in the project. The reviewer found 10 bugs, including a **critical one I never would have caught**: the leader-finding sort was backwards. Vehicles were sorted descending by position, but the scan loop found the first match scanning from the top — meaning it found the **farthest** vehicle ahead, not the nearest. The IDM was computing gaps to the wrong car. The hard clamp I'd added was the only thing preventing total chaos.

The reviewer also found that resetting the simulation didn't clear stale vehicle IDs from the shared network segments, causing ghost vehicles to corrupt leader-finding after reset.

Amir asked if I could have found these bugs myself. Honestly: probably not as well. The sort order bug was inherited from the original code and I never questioned it because I was focused on adding new features. A fresh agent with a "find bugs" prompt reads adversarially rather than confirming what it expects to see.

---

## 2026-03-23/24 — Review Loops 2-5: Systematic Bug Hunting

Amir asked me to run review loops until clean, committing after each one. Here's how it went:

**Loop 2** (7 bugs, 1 critical): The big finding was that multiple WebSocket connections shared the same `_network` object — a browser refresh would corrupt the simulation for everyone. Fixed by allowing only one active simulation at a time and kicking the previous connection. Also found that vehicle overflow past very short segments caused phantom red-light braking (the vehicle thought it was past the end of a segment and saw a "red light" that didn't exist). Fixed with a while loop for multi-segment transitions.

**Loop 3** (4 bugs, 1 medium): The traffic light `offset` parameter was computed and stored but never actually applied — all intersections were cycling in perfect lockstep. This made the simulation unrealistically uniform. Fixed by initializing phase and elapsed time from the offset. Also found a potential XSS issue — the inspect panel used `innerHTML` with unsanitized OSM road names. Switched to DOM-based text content.

**Loop 4** (3 bugs, 1 medium): Click handlers were being registered on every WebSocket reconnect, causing N inspect requests per click after N reconnects. Fixed by only registering handlers once. Also fixed the speed history sparkline not clearing on reconnect.

**Loop 5**: **Clean.** No bugs found. The reviewer confirmed all patterns are correct.

### Totals across all review loops
- **24 bugs found and fixed** (3 critical, 5 medium, 16 low)
- **5 review loops** to reach a clean state
- Each loop got progressively cleaner: 10 → 7 → 4 → 3 → 0

The key insight: a separate review agent with a bug-hunting prompt finds things the author agent won't. Worth making this a standard step after any non-trivial implementation.

---

## 2026-03-24 — Session 3: Performance Fix (WebSocket Backpressure)

Amir started the app and reported "it gets stuck after 10s." I investigated by adding timing instrumentation to the server's sim loop. The simulation step itself was fast (2.5ms), but `serialize_frame()` + `json.dumps()` was taking 16.8ms and producing **134KB frames**. At 20fps, that's 2.7MB/s of WebSocket traffic — `send_json` was backing up faster than the browser could consume it, creating backpressure that eventually froze the loop.

The root cause: every frame included full GeoJSON polygons for all ~430 vehicles — 6 coordinate pairs per arrow polygon, all computed server-side. That's a lot of geometry to serialize, send, and parse 20 times per second.

The fix was to split the work: the server now sends compact arrays of `[vid, lon, lat, bearing, speed, lane, lanes]` (7 numbers per vehicle), and the browser computes the arrow polygons client-side. I moved `arrowPolygon()` and `vehiclesToGeoJSON()` to `web/js/app.js`, using the same geometry math that was already in `state_serializer.py`.

Results: frame size dropped from 134KB to ~31KB (4.3× reduction), serialization time from 16.8ms to 2.9ms. But the freeze actually had two layers — the server was fine, but the browser couldn't keep up rendering 430 polygon computations + MapLibre `setData` at 15fps. Fixed by adding `requestAnimationFrame` throttling (skip frames if still rendering the previous one) and reducing to 10fps.

---

## 2026-03-24 — Session 3 (cont.): Signal Clustering & Intersection Model

After a high-level feature review, Amir zoomed into an intersection and pointed out two problems: (1) the traffic light indicators were unclear — thin lines blending with roads and vehicles, cryptic inspect panel showing "N-S edges: 4" instead of actual road names; and (2) several traffic lights in the same physical intersection were split into different controllers because OSM has multiple signal nodes per intersection.

I ran a dedicated review agent for the high-level analysis, which identified ~16 feature gaps across realism, analysis, and visualization. But the immediate priority was fixing the signal display.

**Clustering fix:** The original clustering used a greedy 40m radius — not transitive. If A was near B and B near C but A far from C, they wouldn't merge. I replaced it with union-find (connected components) and increased the radius to 60m. This handles large intersections properly.

**Intersection expansion:** But Amir noticed a deeper problem — even single-signal intersections had short 2-5m segments that vehicles would stop on mid-intersection. The issue was that OSM represents intersections as clusters of nodes connected by very short edges, and only some nodes are tagged as signals. I proposed expanding clusters via BFS along short segments to absorb nearby junction nodes. Amir caught a potential flaw: roads with many short segments could chain-expand and swallow entire roads. So I added two constraints: (1) only absorb nodes with 3+ connections (actual junction nodes, not mid-road geometry nodes), and (2) cap total distance at 40m from the nearest signal. This naturally stops at intersection boundaries.

**Intra-cluster bypass:** Added a check in `TrafficLightManager.is_green()` — if both source and destination of a segment belong to the same controller, always return green. Vehicles stop at the cluster entry point, not inside the intersection.

**Visual improvements:** Indicator lines now render per-controller at the cluster centroid (not per-node), are thicker (7px vs 3px), and use rounded caps. The inspect panel shows phase groups with road names, colored state blocks, and timing (cycle length, time remaining). Much clearer than "Phase 0, N-S edges: 4."
