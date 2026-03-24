# TrafficFlow Refactoring & Bug Fix Plan

**Created:** 2026-03-24
**Status:** In Progress

This plan addresses all findings from the 6-reviewer audit (simulation physics, traffic signals, network/data, server/WebSocket, frontend/UX, architecture/design). Issues are organized into phases that build on each other — earlier phases unlock later ones.

---

## Phase 1: Critical Bug Fixes (No Refactoring)

Fix bugs that cause incorrect behavior or crashes, without changing architecture.

### 1.1 Vehicle ID reuse after reset
- **File:** `server.py:140-147`
- **Bug:** On reset, `seg.vehicles.clear()` wipes segment lists but the new `Simulation` starts `_next_vid` at 0. If any stale references survive (e.g., in a frame being serialized concurrently), IDs collide.
- **Fix:** Pass a `vid_offset` to `Simulation.__init__` that continues from the previous sim's `_next_vid`. Track this in server.py.

### 1.2 Reset race condition
- **File:** `server.py:140-147`
- **Bug:** Reset sets `paused = True`, then `await asyncio.sleep(0.1)` hoping `sim_loop` pauses — but there's no guarantee the loop has reached its check point. The old `sim` can still be stepping while segments are cleared.
- **Fix:** Cancel the `loop_task`, await its cancellation, clear segments, create new sim, start new loop task.

### 1.3 Task cancellation not awaited
- **File:** `server.py:165`
- **Bug:** `loop_task.cancel()` in the finally block is not awaited. The task may continue running briefly after the WebSocket closes.
- **Fix:** `loop_task.cancel()` followed by `try: await loop_task except asyncio.CancelledError: pass`.

### 1.4 Spillback hard clamp disabled for cross-segment leaders
- **File:** `simulation.py:155`
- **Bug:** `leader_pos = None` disables hard clamp when leader is on a different segment. Vehicles can overlap across segment boundaries.
- **Fix:** Track `leader_on_same_segment` boolean separately from `leader_pos`. Only hard-clamp when leader is on same segment, but don't set `leader_pos = None` — the IDM alone should handle cross-segment gaps.

### 1.5 Free-flow gap returns `self.speed` as leader speed
- **File:** `vehicle.py:155`
- **Bug:** When no leader, `_effective_gap_and_speed` returns `(1e6, self.speed)`. This is actually correct for IDM free-flow (dv=0, huge gap → max accel). Not a bug, but document it.
- **Fix:** Add comment explaining the intentional behavior.

### 1.6 Missing `default` in frontend switch
- **File:** `web/js/app.js:138-148`
- **Bug:** `switch (msg.type)` has no default case. Unknown message types are silently ignored.
- **Fix:** Add `default: console.warn("Unknown message type:", msg.type);`

### 1.7 Unhandled JSON parse in WebSocket onmessage
- **File:** `web/js/app.js:137`
- **Bug:** `JSON.parse(event.data)` can throw if server sends malformed data.
- **Fix:** Wrap in try/catch.

---

## Phase 2: Constants & Configuration

Centralize all hardcoded constants into a config system. This unlocks scenario comparison later.

### 2.1 Create `SimConfig` dataclass
- **New file:** `src/config.py`
- Group all simulation parameters:
  - IDM: `v0`, `T`, `a`, `b`, `s0`, `delta`
  - Vehicle: `length`, `width`, `stop_margin`
  - Signal: `green_duration`, `yellow_duration`, `cluster_radius`, `expand_seg_max`, `expand_dist_max`
  - Simulation: `dt`, `spawn_interval`, `num_vehicles`, `seed`
  - Lane: `lane_width`
- Load from JSON file or use defaults
- Pass through constructor chain: `server → Simulation → Vehicle, TrafficLightManager`

### 2.2 Remove module-level constants
- **Files:** `vehicle.py`, `simulation.py`, `traffic_light.py`, `state_serializer.py`
- Replace `IDM_V0`, `IDM_T`, etc. with `self.config.v0`, `self.config.T`, etc.
- Remove `VEHICLE_LENGTH` from `state_serializer.py` (use config)
- `DT`, `SPAWN_INTERVAL` → from config
- `GREEN_DURATION`, `YELLOW_DURATION`, `CLUSTER_RADIUS_M` → from config

### 2.3 Add `--config` CLI flag
- **File:** `server.py`, `main.py`
- Accept optional JSON config file path
- Merge CLI args over file config over defaults

---

## Phase 3: Encapsulate Vehicle-Segment Tracking

The most impactful architectural fix. Currently vehicle-to-segment mapping is mutated from 3 different files.

### 3.1 Create `VehicleTracker` class
- **New file:** `src/vehicle_tracker.py`
- Owns the `Dict[int, Vehicle]` and `segment.vehicles` lists
- API:
  - `add_vehicle(vehicle, segment)` — adds to both dict and segment list
  - `remove_vehicle(vid)` — removes from both
  - `move_vehicle(vid, old_seg, new_seg)` — atomic segment transition
  - `get_vehicles_on_segment(seg_id) → List[Vehicle]` — sorted by position
  - `clear()` — reset all tracking
- Single source of truth — no more scattered `seg.vehicles.append/remove`

### 3.2 Update Vehicle to use tracker
- **File:** `vehicle.py`
- Remove direct `seg.vehicles.remove(self.vid)` and `seg.vehicles.append(self.vid)` calls
- Vehicle's `step()` returns segment transition events; caller (Simulation) applies them via tracker

### 3.3 Update Simulation to use tracker
- **File:** `simulation.py`
- Replace manual segment list manipulation with tracker API
- `seg_vehicles` dict (lines 92-100) becomes `tracker.get_vehicles_on_segment()`
- Dead vehicle cleanup uses `tracker.remove_vehicle()`

### 3.4 Update server.py
- Remove `seg.vehicles.clear()` calls — use `tracker.clear()` via simulation

---

## Phase 4: Break Up Simulation.step()

Extract the 100-line monolith into focused components.

### 4.1 Extract leader-finding into `FollowerService`
- **New file:** `src/follower.py`
- `find_leader(vehicle, tracker, light_mgr) → (gap, speed, same_segment)`
- Contains:
  - Same-segment leader scan (current lines 114-129)
  - Cross-segment spillback lookahead (current lines 131-168)
  - "Don't block the box" logic
- Testable in isolation

### 4.2 Extract collision resolution
- Move hard clamp logic (lines 173-178) into a `resolve_collisions()` method
- Called after all vehicles step

### 4.3 Extract vehicle lifecycle
- Move spawn/despawn logic into methods:
  - `_cleanup_dead(dead_list)` — remove and respawn
  - Keep `_spawn_vehicle()` as-is

### 4.4 Simplified `step()` becomes orchestrator
```python
def step(self):
    self.light_mgr.step(self.dt)
    leaders = self.follower.compute_all_leaders(self.tracker)
    for vid, v in self.vehicles.items():
        gap, speed, same_seg = leaders.get(vid, (None, None, False))
        v.step(self.dt, gap, speed)
    self.resolve_collisions(leaders)
    self.cleanup_dead()
    self.time += self.dt
    self._record_stats()
```

---

## Phase 5: Decouple Simulation from Server

Enable batch/CLI usage without WebSocket.

### 5.1 Create `SimulationRunner`
- **New file:** `src/runner.py`
- Wraps `Simulation` with lifecycle management:
  - `create(network, config) → Simulation`
  - `reset() → Simulation` (handles cleanup properly)
  - `step(n=1)` — advance n steps
  - `get_frame() → dict` — serialized state
  - `export_trajectories(path)` — CSV export

### 5.2 Simplify server.py
- Server creates a `SimulationRunner`, delegates all sim management to it
- Reset becomes `runner.reset()` — no more manual segment clearing
- Remove sim lifecycle code from WebSocket handler

### 5.3 Update main.py
- Use `SimulationRunner` for headless mode
- Add `--export` flag for trajectory CSV output

---

## Phase 6: Traffic Light Controller Interface

Make signal control pluggable for future adaptive controllers.

### 6.1 Extract `SignalController` protocol
- **New file:** `src/signal_controller.py`
- Protocol/ABC:
  - `step(dt)`
  - `is_green(edge_id) → bool`
  - `get_state() → dict` (for serialization)

### 6.2 Rename current logic to `FixedTimingController`
- Current `TrafficLight` class becomes `FixedTimingController`
- Implements `SignalController` protocol

### 6.3 Fix diagonal road phase assignment
- **File:** `traffic_light.py:62`
- Currently `45 <= b_norm < 135` means exactly 45° goes to E-W. Use a dominant-axis check instead:
  - `abs(sin(bearing)) > abs(cos(bearing))` → E-W, else N-S
- This handles all angles consistently

### 6.4 Fix intra-cluster bypass scope
- **File:** `traffic_light.py:307-310`
- Current bypass returns green for ANY segment between two nodes in the same cluster. This is too broad — a non-signal junction node absorbed into a cluster shouldn't get a free pass on segments entering it from outside.
- Fix: only bypass when BOTH source and destination are in the cluster AND the segment is short (< EXPAND_SEG_MAX_M). External approaches to absorbed nodes should still check the real signal.

---

## Phase 7: Network & Data Quality

### 7.1 Add attribute defaults with logging
- **File:** `network.py`
- Already has `DEFAULT_SPEED_MS` and `DEFAULT_LANES` — verify they're always applied
- Log a warning count at build time: "X segments used default speed, Y used default lanes"
- Clamp `length` to minimum 1.0m (already done on line 78)

### 7.2 Validate network connectivity
- **File:** `network.py`
- After build, check for disconnected components using `nx.strongly_connected_components(G)`
- If multiple components exist, keep the largest and log a warning
- This prevents `shortest_path` failures and vehicle spawn retries

### 7.3 Add `travel_time` edge weight
- **File:** `map_loader.py`
- `ox.add_edge_travel_times(G)` already adds this (line 65)
- Verify it's present on all edges; fall back to `length / speed` for missing ones
- **File:** `network.py:133` — `shortest_path` uses `weight="travel_time"`, confirm this attribute exists

### 7.4 Fix `speed_limit` of 0 fallback
- **File:** `vehicle.py:83`
- `min(IDM_V0, seg.speed_limit) or IDM_V0` — the `or` works but is unclear
- Change to explicit: `v0 = seg.speed_limit if seg.speed_limit > 0 else IDM_V0; v0 = min(v0, IDM_V0)`

---

## Phase 8: Server Hardening

### 8.1 Fix active_ws race
- **File:** `server.py:77-86`
- Use the `_active_sim_lock` that already exists but is never used
- Wrap the WebSocket handler's sim creation in `async with _active_sim_lock`

### 8.2 Add exception handling in sim_loop
- **File:** `server.py:104-122`
- Catch and log exceptions in the loop body (not just WebSocket errors)
- On unexpected exception, send an error frame to the client

### 8.3 Add health check endpoint
- **File:** `server.py`
- `GET /health` → `{"status": "ok", "corridor": name, "vehicles": N}`

---

## Phase 9: Frontend Robustness

### 9.1 Guard against null/undefined
- Wrap `JSON.parse` in try/catch (1.7 above)
- Guard `indicatorGeoJSON` access with null check (already has one at line 301)
- Guard bounds calculation for empty vehicle array (line 253-260)

### 9.2 Add visual feedback for connection state
- Show a small connection indicator (green dot = connected, red = disconnected, yellow = reconnecting)
- Overlay "PAUSED" text when paused

### 9.3 Dynamic sparkline Y-axis
- Currently Y-axis scales to `Math.max(...speedHistory, 1)` — rescales every frame
- Use a fixed max (e.g., 60 km/h) with auto-scale only if exceeded
- Add subtle grid lines and axis labels

---

## Phase 10: Testing

### 10.1 Unit tests for Vehicle IDM
- **New file:** `tests/test_vehicle.py`
- Test free-flow acceleration
- Test car-following deceleration
- Test red light stopping
- Test segment transition

### 10.2 Unit tests for TrafficLight
- **New file:** `tests/test_traffic_light.py`
- Test phase transitions (green → yellow → green+switch)
- Test `is_green` for each phase
- Test offset initialization

### 10.3 Unit tests for FollowerService
- **New file:** `tests/test_follower.py`
- Test same-segment leader finding
- Test cross-segment spillback
- Test intra-cluster bypass

### 10.4 Integration test for Simulation
- **New file:** `tests/test_simulation.py`
- Create a simple 3-node network
- Run 100 steps, verify no crashes
- Verify vehicle counts stay stable

---

## Progress Tracker

| Phase | Task | Status |
|-------|------|--------|
| 1.1 | Vehicle ID reuse after reset | DONE |
| 1.2 | Reset race condition | DONE |
| 1.3 | Task cancellation not awaited | DONE |
| 1.4 | Spillback hard clamp fix | DONE |
| 1.5 | Document free-flow behavior | DONE |
| 1.6 | Frontend switch default | DONE |
| 1.7 | Frontend JSON parse guard | DONE |
| 2.1 | Create SimConfig | DONE |
| 2.2 | Remove module-level constants | DONE |
| 2.3 | Add --config CLI flag | DONE |
| 3.1 | Create VehicleTracker | DONE |
| 3.2 | Update Vehicle for tracker | DONE |
| 3.3 | Update Simulation for tracker | DONE |
| 3.4 | Update server.py for tracker | DONE |
| 4.1 | Extract FollowerService | DONE |
| 4.2 | Extract collision resolution | DONE (kept inline, simplified) |
| 4.3 | Extract vehicle lifecycle | DONE (via tracker) |
| 4.4 | Simplify step() | DONE |
| 5.1 | Create SimulationRunner | DONE |
| 5.2 | Simplify server.py | DONE |
| 5.3 | Update main.py | DONE |
| 6.1 | SignalController protocol | DONE |
| 6.2 | FixedTimingController | DONE (TrafficLight implements protocol) |
| 6.3 | Fix diagonal phase assignment | DONE |
| 6.4 | Fix intra-cluster bypass scope | DONE |
| 7.1 | Attribute defaults with logging | DONE |
| 7.2 | Validate network connectivity | DONE |
| 7.3 | Verify travel_time weights | DONE (verified, no change needed) |
| 7.4 | Fix speed_limit fallback | DONE |
| 8.1 | Fix active_ws race | DONE |
| 8.2 | Exception handling in sim_loop | DONE |
| 8.3 | Health check endpoint | DONE |
| 9.1 | Guard null/undefined | DONE |
| 9.2 | Connection state indicator | DONE |
| 9.3 | Dynamic sparkline Y-axis | DONE |
| 10.1 | Unit tests: Vehicle IDM | DONE (5 tests) |
| 10.2 | Unit tests: TrafficLight | DONE (6 tests) |
| 10.3 | Unit tests: FollowerService | DONE (4 tests) |
| 10.4 | Integration test: Simulation | DONE (6 tests) |
