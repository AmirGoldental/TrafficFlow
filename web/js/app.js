/**
 * TrafficFlow Web Dashboard
 *
 * Single-file app: WebSocket connection, MapLibre map, controls, stats.
 */

// ------------------------------------------------------------------ state
let ws = null;
let paused = false;
let map = null;
let networkLoaded = false;
let speedHistory = [];
const MAX_HISTORY = 120;

// Signal color lookup: "nodeId-edgeId" -> color
let signalColorMap = {};
// Keep our own copy of indicator GeoJSON (avoid accessing MapLibre internals)
let indicatorGeoJSON = null;

// ------------------------------------------------------------------ init

document.addEventListener("DOMContentLoaded", () => {
    initMap();
    initControls();
});


// ------------------------------------------------------------------ map

function initMap() {
    map = new maplibregl.Map({
        container: "map",
        style: {
            version: 8,
            name: "Dark",
            sources: {
                "osm-tiles": {
                    type: "raster",
                    tiles: [
                        "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
                        "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",
                    ],
                    tileSize: 256,
                    attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
                },
            },
            layers: [{
                id: "background",
                type: "background",
                paint: { "background-color": "#0d0d1a" },
            }, {
                id: "osm-tiles",
                type: "raster",
                source: "osm-tiles",
                paint: { "raster-opacity": 0.6 },
            }],
        },
        center: [-71.083, 42.319],
        zoom: 15,
        attributionControl: true,
    });

    map.addControl(new maplibregl.NavigationControl(), "top-left");

    map.on("load", () => {
        connectWebSocket();
    });
}


// ------------------------------------------------------------------ websocket

function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/ws/simulation`);

    ws.onopen = () => {
        console.log("WebSocket connected");
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        switch (msg.type) {
            case "network":
                onNetwork(msg);
                break;
            case "frame":
                onFrame(msg);
                break;
            case "inspect_result":
                onInspectResult(msg);
                break;
        }
    };

    ws.onclose = () => {
        console.log("WebSocket closed, reconnecting in 2s...");
        setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = (err) => {
        console.error("WebSocket error:", err);
    };
}


// ------------------------------------------------------------------ network (once)

function onNetwork(msg) {
    // Remove existing layers/sources on reconnect
    for (const layerId of ["vehicles-outline", "vehicles", "indicators", "roads"]) {
        if (map.getLayer(layerId)) map.removeLayer(layerId);
    }
    for (const srcId of ["vehicles", "indicators", "roads"]) {
        if (map.getSource(srcId)) map.removeSource(srcId);
    }

    // Store indicator data for per-frame color updates
    indicatorGeoJSON = msg.indicators;

    // Add road network layer
    map.addSource("roads", {
        type: "geojson",
        data: msg.roads,
    });
    map.addLayer({
        id: "roads",
        type: "line",
        source: "roads",
        paint: {
            "line-color": "#3a3a5c",
            "line-width": [
                "interpolate", ["linear"], ["get", "lanes"],
                1, 1.5,
                2, 2.5,
                4, 4,
            ],
            "line-opacity": 0.7,
        },
    });

    // Signal directional indicators
    map.addSource("indicators", {
        type: "geojson",
        data: msg.indicators,
    });
    map.addLayer({
        id: "indicators",
        type: "line",
        source: "indicators",
        paint: {
            "line-color": "#f5a623",  // will be updated per frame
            "line-width": 3,
            "line-opacity": 0.9,
        },
    });

    // Vehicles layer — arrow polygons (updated every frame)
    map.addSource("vehicles", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
    });
    map.addLayer({
        id: "vehicles",
        type: "fill",
        source: "vehicles",
        paint: {
            "fill-color": [
                "interpolate", ["linear"], ["get", "speed"],
                0, "#ff3333",
                5, "#ffdd00",
                10, "#00ff88",
            ],
            "fill-opacity": 0.9,
        },
    });
    map.addLayer({
        id: "vehicles-outline",
        type: "line",
        source: "vehicles",
        paint: {
            "line-color": "rgba(255,255,255,0.3)",
            "line-width": 0.5,
        },
    });

    // Fit bounds to network
    const coords = msg.roads.features.flatMap(f => f.geometry.coordinates);
    if (coords.length > 0) {
        const bounds = coords.reduce(
            (b, c) => b.extend(c),
            new maplibregl.LngLatBounds(coords[0], coords[0])
        );
        map.fitBounds(bounds, { padding: 60 });
    }

    // Click interactions
    setupClickHandlers();

    networkLoaded = true;
    document.getElementById("loading-overlay").classList.add("hidden");
}


// ------------------------------------------------------------------ frame (per tick)

function onFrame(msg) {
    if (!networkLoaded) return;

    // Update vehicle positions
    const vehicleSrc = map.getSource("vehicles");
    if (vehicleSrc) {
        vehicleSrc.setData(msg.vehicles);
    }

    // Update signal indicator colours
    if (msg.signals && msg.signals.length > 0) {
        updateSignalIndicators(msg.signals);
    }

    // Update stats
    updateStats(msg);
}

function updateSignalIndicators(signals) {
    if (!indicatorGeoJSON || !indicatorGeoJSON.features) return;

    // Build color lookup
    signalColorMap = {};
    for (const s of signals) {
        const key = `${s.node_id}-${s.edge_id}`;
        signalColorMap[key] = s.color === "green" ? "#00ff88"
                            : s.color === "yellow" ? "#ffdd00"
                            : "#ff3333";
    }

    // Update our own copy of indicator GeoJSON with colors
    for (const feat of indicatorGeoJSON.features) {
        const key = `${feat.properties.node_id}-${feat.properties.edge_id}`;
        feat.properties.color = signalColorMap[key] || "#f5a623";
    }

    const src = map.getSource("indicators");
    if (!src) return;
    src.setData(indicatorGeoJSON);

    // Use data-driven paint (only need to set once, but idempotent)
    map.setPaintProperty("indicators", "line-color", [
        "coalesce", ["get", "color"], "#f5a623"
    ]);
}


// ------------------------------------------------------------------ stats

function updateStats(msg) {
    const time = msg.time || 0;
    const stats = msg.stats || {};

    document.getElementById("stat-time").textContent = `${time.toFixed(0)}s`;
    document.getElementById("stat-vehicles").textContent = stats.active_vehicles || 0;
    document.getElementById("stat-speed").textContent = `${(stats.avg_speed_kmh || 0).toFixed(1)} km/h`;
    document.getElementById("sim-time").textContent = `t = ${time.toFixed(0)}s`;

    // Speed history sparkline
    speedHistory.push(stats.avg_speed_kmh || 0);
    if (speedHistory.length > MAX_HISTORY) speedHistory.shift();
    drawSparkline();
}

function drawSparkline() {
    const canvas = document.getElementById("sparkline");
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    if (speedHistory.length < 2) return;

    const max = Math.max(...speedHistory, 1);
    const step = w / (MAX_HISTORY - 1);

    // Fill gradient
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, "rgba(0,255,136,0.15)");
    grad.addColorStop(1, "rgba(0,255,136,0)");

    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let i = 0; i < speedHistory.length; i++) {
        const x = i * step;
        const y = h - (speedHistory[i] / max) * (h - 4);
        ctx.lineTo(x, y);
    }
    ctx.lineTo((speedHistory.length - 1) * step, h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
    ctx.beginPath();
    for (let i = 0; i < speedHistory.length; i++) {
        const x = i * step;
        const y = h - (speedHistory[i] / max) * (h - 4);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = "#00ff88";
    ctx.lineWidth = 1.5;
    ctx.stroke();
}


// ------------------------------------------------------------------ controls

function initControls() {
    const btnPlay = document.getElementById("btn-play");
    const btnReset = document.getElementById("btn-reset");
    const speedSlider = document.getElementById("speed-slider");
    const speedLabel = document.getElementById("speed-label");

    btnPlay.addEventListener("click", () => {
        paused = !paused;
        document.getElementById("play-icon").textContent = paused ? "▶" : "⏸";
        btnPlay.classList.toggle("active", !paused);
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "control",
                action: paused ? "pause" : "play",
            }));
        }
    });

    btnReset.addEventListener("click", () => {
        paused = false;
        document.getElementById("play-icon").textContent = "⏸";
        btnPlay.classList.add("active");
        speedHistory = [];
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "control", action: "reset" }));
        }
    });

    speedSlider.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        speedLabel.textContent = `${val}×`;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "control",
                action: "speed",
                value: val,
            }));
        }
    });
}


// ------------------------------------------------------------------ click handlers

function setupClickHandlers() {
    // Vehicle click
    map.on("click", "vehicles", (e) => {
        if (!e.features || !e.features.length) return;
        const props = e.features[0].properties;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "inspect",
                target: "vehicle",
                id: props.vid,
            }));
        }
    });

    // Signal indicator click
    map.on("click", "indicators", (e) => {
        if (!e.features || !e.features.length) return;
        const props = e.features[0].properties;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: "inspect",
                target: "signal",
                id: props.node_id,
            }));
        }
    });

    // Cursor changes
    for (const layer of ["vehicles", "indicators"]) {
        map.on("mouseenter", layer, () => {
            map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layer, () => {
            map.getCanvas().style.cursor = "";
        });
    }
}


// ------------------------------------------------------------------ inspect

function appendStatRows(container, rows) {
    for (const [label, value, color] of rows) {
        const row = document.createElement("div");
        row.className = "stat-row";
        const labelEl = document.createElement("span");
        labelEl.className = "stat-label";
        labelEl.textContent = label;
        const valueEl = document.createElement("span");
        valueEl.className = "stat-value";
        valueEl.textContent = value;
        if (color) valueEl.style.color = color;
        row.appendChild(labelEl);
        row.appendChild(valueEl);
        container.appendChild(row);
    }
}

function onInspectResult(msg) {
    const panel = document.getElementById("inspect-panel");
    const title = document.getElementById("inspect-title");
    const content = document.getElementById("inspect-content");

    panel.style.display = "block";

    if (msg.target === "vehicle") {
        const d = msg.data;
        title.textContent = `Vehicle #${d.vid}`;
        content.innerHTML = "";
        appendStatRows(content, [
            ["Speed", `${d.speed_kmh} km/h`],
            ["Acceleration", `${d.accel} m/s\u00B2`],
            ["Route", d.route_progress],
            ["Distance", `${d.distance_total_m} m`],
            ["Road", d.current_road || "\u2014"],
            ["Position", d.segment_pos],
        ]);
    } else if (msg.target === "signal") {
        const d = msg.data;
        const stateColor = d.state === "green" ? "#00ff88" : d.state === "yellow" ? "#ffdd00" : "#ff3333";
        title.textContent = `Signal #${d.node_id}`;
        content.innerHTML = "";
        appendStatRows(content, [
            ["State", d.state, stateColor],
            ["Phase", d.current_phase],
            ["Cluster nodes", d.controller_nodes?.length || 1],
            ["N-S edges", d.phase_0_edges],
            ["E-W edges", d.phase_1_edges],
            ["Roads", (d.incoming_roads || []).join(", ") || "\u2014"],
        ]);
    }
}
