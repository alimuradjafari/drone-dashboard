"""FastAPI backend for MAVLink telemetry."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
import json
import time

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from config import (
    API_KEY,
    BATTERY_CAPACITY_MAH,
    CHARGING_STALE_AFTER_SECONDS,
    CONNECTION_TARGETS,
    CORS_ORIGINS,
    DRONE_ID,
    HEARTBEAT_STALE_AFTER_SECONDS,
    INITIAL_PACKET_TIMEOUT,
    MAVLINK_SOURCE_COMPONENT,
    MAVLINK_SOURCE_SYSTEM,
    RECONNECT_DELAY_SECONDS,
    REQUEST_STREAM_RATE_HZ,
    REQUIRE_HEARTBEAT,
    STALE_AFTER_SECONDS,
)
from drone_connection import DroneConnection, FleetManager


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


drone = FleetManager(
    stale_after_seconds=STALE_AFTER_SECONDS,
    heartbeat_stale_after_seconds=HEARTBEAT_STALE_AFTER_SECONDS,
    require_heartbeat=REQUIRE_HEARTBEAT,
    battery_capacity_mah=BATTERY_CAPACITY_MAH,
    request_stream_rate_hz=REQUEST_STREAM_RATE_HZ,
    source_system=MAVLINK_SOURCE_SYSTEM,
    source_component=MAVLINK_SOURCE_COMPONENT,
)


DISCONNECTED_TELEMETRY_TEMPLATE = {
    "droneId": DRONE_ID,
    "connectionStatus": "Disconnected",
    "armed": False,
    "flightMode": "Unknown",
    "missionStatus": "Unknown",
    "connectionType": "None",
    "position": {
        "lat": 0.0,
        "lng": 0.0,
        "alt": 0.0,
        "heading": 0.0,
    },
    "navigation": {
        "groundSpeed": 0.0,
        "airSpeed": 0.0,
        "distanceFromHome": 0.0,
        "distanceToWaypoint": 0.0,
    },
    "attitude": {
        "roll": 0.0,
        "pitch": 0.0,
        "yaw": 0.0,
    },
    "gps": {
        "fixType": "No GPS",
        "satellites": 0,
        "hdop": 0.0,
    },
    "battery": {
        "percent": 0.0,
        "voltage": 0.0,
        "current": 0.0,
        "capacity": 0,
        "timeLeft": 0.0,
        "health": "Unknown",
        "status": "unknown",
        "consumedMah": 0,
    },
    "charging": {
        "docked": False,
        "status": "Unknown",
        "progress": 0.0,
        "voltage": 0.0,
        "current": 0.0,
        "eta": 0.0,
    },
    "communication": {
        "rssi": 0,
        "linkStatus": "Disconnected",
        "packetLoss": 0.0,
        "dataAge": None,
        "quality": "Offline",
        "heartbeatStatus": "Missing",
        "heartbeatAge": None,
        "lastMessageType": None,
        "packetCount": 0,
        "badDataCount": 0,
        "lastUpdate": "",
    },
}


class ChargingTelemetry(BaseModel):
    docked: bool = False
    status: str = "Unknown"
    progress: float = Field(default=0, ge=0, le=100)
    voltage: float = Field(default=0, ge=0)
    current: float = Field(default=0, ge=0)
    eta: float = Field(default=0, ge=0)


charging_telemetry = ChargingTelemetry()
charging_last_update: float | None = None
latest_telemetry = deepcopy(DISCONNECTED_TELEMETRY_TEMPLATE)
latest_fleet: dict[str, dict] = {}
active_connections: list[WebSocket] = []
fleet_connections: list[WebSocket] = []


def get_charging_data() -> dict:
    """Return station telemetry with freshness metadata."""
    payload = charging_telemetry.model_dump()
    if charging_last_update is None:
        return {**payload, "online": False, "status": "Offline", "lastUpdate": None}

    age = max(0.0, time.time() - charging_last_update)
    online = age <= CHARGING_STALE_AFTER_SECONDS
    if not online:
        payload.update({"docked": False, "status": "Offline"})
    return {
        **payload,
        "online": online,
        "lastUpdate": datetime.fromtimestamp(charging_last_update).isoformat(),
        "dataAge": round(age, 2),
    }


def _battery_health(percent: float, battery_status: str) -> str:
    if battery_status in {"unknown", "no_battery"}:
        return "Unknown"
    if percent > 30:
        return "Healthy"
    if percent > 15:
        return "Low"
    return "Critical"


def get_telemetry_data() -> dict:
    """Return live telemetry for the primary drone (backward-compat)."""
    fleet = drone.get_fleet()
    # Pick first active drone as the primary
    for drone_data in fleet.values():
        if drone_data.get("connectionStatus") == "Connected":
            battery = drone_data.get("battery", {})
            percent = float(battery.get("percent", 0.0) or 0.0)
            voltage = float(battery.get("voltage", 0.0) or 0.0)
            current = float(battery.get("current", 0.0) or 0.0)
            consumed = int(battery.get("consumedMah", 0) or 0)
            capacity_remaining = float(battery.get("capacityRemaining", 0.0) or 0.0)
            battery_status = str(battery.get("status", "unknown"))
            time_left = float(battery.get("flightTimeMinutes", 0.0) or 0.0)
            packet_age = drone_data.get("packetAge")
            quality = (
                "Live"
                if packet_age is not None and packet_age <= STALE_AFTER_SECONDS
                else "Stale"
            )
            return {
                "droneId": drone_data.get("droneId", DRONE_ID),
                "connectionStatus": "Connected",
                "armed": drone_data.get("armed", False),
                "flightMode": drone_data.get("flightMode", "Unknown"),
                "missionStatus": "En Route" if drone_data.get("armed") else "Idle",
                "connectionType": drone_data.get("connectionType", "WIFI"),
                "position": drone_data.get("position", DISCONNECTED_TELEMETRY_TEMPLATE["position"]),
                "navigation": drone_data.get("navigation", DISCONNECTED_TELEMETRY_TEMPLATE["navigation"]),
                "attitude": drone_data.get("attitude", DISCONNECTED_TELEMETRY_TEMPLATE["attitude"]),
                "gps": drone_data.get("gps", DISCONNECTED_TELEMETRY_TEMPLATE["gps"]),
                "battery": {
                    "percent": round(percent, 1),
                    "voltage": round(voltage, 2),
                    "current": round(current, 2),
                    "capacity": round(capacity_remaining, 0),
                    "timeLeft": round(time_left, 1),
                    "health": _battery_health(percent, battery_status),
                    "status": battery_status,
                    "consumedMah": consumed,
                },
                "charging": get_charging_data(),
                "communication": {
                    "rssi": drone_data.get("communication", {}).get("rssi", 0),
                    "linkStatus": drone_data.get("connectionType", "WIFI"),
                    "packetLoss": drone_data.get("communication", {}).get("packetLoss", 0.0),
                    "dataAge": packet_age,
                    "quality": quality,
                    "heartbeatStatus": drone_data.get("heartbeatStatus", "Missing"),
                    "lastUpdate": datetime.now().isoformat(),
                },
            }

    # Fallback: disconnected payload
    diag = drone.get_diagnostics()
    disconnected = deepcopy(DISCONNECTED_TELEMETRY_TEMPLATE)
    disconnected["charging"] = get_charging_data()
    disconnected["communication"].update({
        "heartbeatStatus": diag.get("heartbeatStatus", "Missing"),
        "lastUpdate": datetime.now().isoformat(),
    })
    return disconnected


def get_fleet_payload() -> dict:
    """Build the fleet WebSocket payload including summary and per-drone data."""
    fleet = drone.get_fleet()
    summary = drone.get_fleet_summary(fleet)
    # Enrich each drone with charging data
    for d in fleet.values():
        d["charging"] = get_charging_data()
        d["missionStatus"] = "En Route" if d.get("armed") else "Idle"
    return {"summary": summary, "drones": fleet}


async def reconnect_single_target(target: str) -> None:
    """Keep one connection target alive, reconnecting as needed."""
    print(f"[{target}] Monitoring started")
    while True:
        try:
            if not drone.is_endpoint_open(target):
                print(f"[{target}] Connecting...")
                connected = await asyncio.to_thread(
                    drone.connect, target, INITIAL_PACKET_TIMEOUT,
                )
                if connected:
                    print(f"[{target}] Live MAVLink traffic established")
                else:
                    print(f"[{target}] No traffic; retrying in {RECONNECT_DELAY_SECONDS:g}s")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{target}] Reconnect error: {exc}")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)


async def update_telemetry_loop() -> None:
    """Refresh the in-memory API payload at 20 Hz."""
    global latest_telemetry, latest_fleet
    while True:
        try:
            latest_telemetry = get_telemetry_data()
            latest_fleet = get_fleet_payload()
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Telemetry update loop error: {exc}")
            await asyncio.sleep(0.2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    print(f"Starting MAVLink fleet backend — {len(CONNECTION_TARGETS)} target(s)")
    print(f"Targets: {', '.join(CONNECTION_TARGETS)}")
    # One reconnect task PER target so COM6 + COM9 + UDP all run in parallel
    tasks = [asyncio.create_task(reconnect_single_target(t)) for t in CONNECTION_TARGETS]
    tasks.append(asyncio.create_task(update_telemetry_loop()))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(drone.disconnect)


app = FastAPI(
    title="Drone Telemetry API (MAVLink)",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.websocket("/ws/telemetry")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    if API_KEY and token != API_KEY:
        await websocket.close(code=1008, reason="Invalid API key")
        return

    await websocket.accept()
    active_connections.append(websocket)
    print(f"Dashboard client connected. Total: {len(active_connections)}")

    try:
        await websocket.send_text(json.dumps(latest_telemetry))
        while True:
            await asyncio.sleep(0.1)
            await websocket.send_text(json.dumps(latest_telemetry))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"WebSocket client error: {exc}")
    finally:
        if websocket in active_connections:
            active_connections.remove(websocket)
        print(f"Dashboard client disconnected. Total: {len(active_connections)}")


@app.get("/")
async def root() -> dict:
    return {
        "status": "online",
        "message": "Drone Telemetry API is running",
    }


@app.get("/api/telemetry/latest")
async def get_latest_telemetry(_: None = Depends(require_api_key)) -> dict:
    return latest_telemetry


@app.get("/api/status", dependencies=[Depends(require_api_key)])
async def get_status() -> dict:
    return {
        **drone.get_diagnostics(),
        "configuredTargets": CONNECTION_TARGETS,
        "requireHeartbeat": REQUIRE_HEARTBEAT,
        "telemetryStaleAfter": STALE_AFTER_SECONDS,
        "heartbeatStaleAfter": HEARTBEAT_STALE_AFTER_SECONDS,
    }


@app.get("/api/fleet", dependencies=[Depends(require_api_key)])
async def get_fleet_endpoint() -> dict:
    """Return real-time telemetry for all active drones in the fleet."""
    return latest_fleet


@app.websocket("/ws/fleet")
async def fleet_websocket_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    """WebSocket that pushes the full fleet payload (all drones) at 10 Hz."""
    if API_KEY and token != API_KEY:
        await websocket.close(code=1008, reason="Invalid API key")
        return
    await websocket.accept()
    fleet_connections.append(websocket)
    print(f"Fleet client connected. Total fleet clients: {len(fleet_connections)}")
    try:
        await websocket.send_text(json.dumps(latest_fleet))
        while True:
            await asyncio.sleep(0.1)
            await websocket.send_text(json.dumps(latest_fleet))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"Fleet WebSocket client error: {exc}")
    finally:
        if websocket in fleet_connections:
            fleet_connections.remove(websocket)
        print(f"Fleet client disconnected. Total fleet clients: {len(fleet_connections)}")


@app.put("/api/charging", dependencies=[Depends(require_api_key)])
async def update_charging(payload: ChargingTelemetry) -> dict:
    global charging_telemetry, charging_last_update
    charging_telemetry = payload
    charging_last_update = time.time()
    return {
        "status": "updated",
        "charging": get_charging_data(),
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
