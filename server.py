"""
FastAPI server for the traffic simulation web dashboard.

Endpoints:
    GET  /              — serves the web UI
    WS   /ws/simulation — streams simulation state in real-time

Run with:
    python server.py [--corridor warren_st] [--vehicles N] [--port 8000]
"""

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

sys.path.insert(0, os.path.dirname(__file__))

from src.map_loader import load_corridor, load_graph, get_traffic_signal_nodes, CORRIDORS
from src.network import RoadNetwork
from src.simulation import Simulation
from src.state_serializer import (
    serialize_network,
    serialize_frame,
    serialize_vehicle_detail,
    serialize_signal_detail,
)

# ------------------------------------------------------------------ globals
_network: RoadNetwork = None
_network_json: dict = None
_corridor_name: str = "warren_st"
_num_vehicles: int = None
_active_sim_lock = None  # asyncio.Lock, created at startup
_active_ws = None        # track the single active WebSocket


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _network, _network_json, _num_vehicles
    print(f"Loading corridor: {_corridor_name}", flush=True)
    G, signals = load_corridor(_corridor_name, force_download=False)
    _network = RoadNetwork(G, signals)
    _network_json = serialize_network(_network)

    if _num_vehicles is None:
        _num_vehicles = max(50, len(_network.segments) // 3)
    print(f"Network ready: {len(_network.segments)} segments, "
          f"{sum(1 for i in _network.intersections.values() if i.is_signal)} signals",
          flush=True)
    print(f"Default vehicles: {_num_vehicles}", flush=True)
    global _active_sim_lock
    _active_sim_lock = asyncio.Lock()
    yield


app = FastAPI(title="TrafficFlow Dashboard", lifespan=lifespan)

# ------------------------------------------------------------------ static files
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


# ------------------------------------------------------------------ websocket

@app.websocket("/ws/simulation")
async def simulation_ws(ws: WebSocket):
    global _active_ws
    await ws.accept()

    # Only one simulation at a time — kick previous connection
    if _active_ws is not None:
        try:
            await _active_ws.close(4000, "replaced by new connection")
        except Exception:
            pass
    _active_ws = ws

    # Clear any stale vehicle IDs from shared network segments
    for seg in _network.segments.values():
        seg.vehicles.clear()
    sim = Simulation(_network, num_vehicles=_num_vehicles)
    paused = False
    speed_mult = 1.0
    target_fps = 10
    frame_interval = 1.0 / target_fps

    # Send static network data
    await ws.send_json({"type": "network", **_network_json})

    # Send initial frame
    await ws.send_json(serialize_frame(sim))

    async def sim_loop():
        nonlocal paused, speed_mult, sim
        while True:
            if paused:
                await asyncio.sleep(0.05)
                continue

            steps = max(1, int(speed_mult))
            for _ in range(steps):
                sim.step()

            frame = serialize_frame(sim)
            try:
                await asyncio.wait_for(ws.send_json(frame), timeout=0.5)
            except asyncio.TimeoutError:
                pass  # drop frame rather than stall
            except Exception:
                return
            await asyncio.sleep(frame_interval)

    loop_task = asyncio.create_task(sim_loop())

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "control":
                action = msg.get("action")
                if action == "pause":
                    paused = True
                elif action == "play":
                    paused = False
                elif action == "speed":
                    speed_mult = max(0.25, min(10.0, float(msg.get("value", 1.0))))
                elif action == "reset":
                    # Pause loop, clear segments, create new sim, resume
                    paused = True
                    await asyncio.sleep(0.1)  # let sim_loop reach its pause point
                    for seg in _network.segments.values():
                        seg.vehicles.clear()
                    sim = Simulation(_network, num_vehicles=_num_vehicles)
                    paused = False

            elif msg_type == "inspect":
                target = msg.get("target")
                if target == "vehicle":
                    vid = int(msg.get("id", -1))
                    detail = serialize_vehicle_detail(sim, vid)
                    await ws.send_json({"type": "inspect_result", "target": "vehicle", "data": detail})
                elif target == "signal":
                    nid = int(msg.get("id", -1))
                    detail = serialize_signal_detail(sim, nid)
                    await ws.send_json({"type": "inspect_result", "target": "signal", "data": detail})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        loop_task.cancel()
        if _active_ws is ws:
            _active_ws = None


# ------------------------------------------------------------------ main

def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="TrafficFlow Web Dashboard")
    parser.add_argument("--corridor", choices=list(CORRIDORS.keys()),
                        default="warren_st")
    parser.add_argument("--vehicles", type=int, default=None)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    global _corridor_name, _num_vehicles
    _corridor_name = args.corridor
    _num_vehicles = args.vehicles

    print(f"\n  TrafficFlow Dashboard")
    print(f"  Open http://{args.host}:{args.port} in your browser\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
