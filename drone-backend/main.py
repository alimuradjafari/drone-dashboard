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
from drone_connection import DroneConnection


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


drone = DroneConnection(
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
latest_telemetry = deepcopy(DISCONNECTED_TELEMETRY_TEMPLATE)
active_connections: list[WebSocket] = []


def _battery_health(percent: float, battery_status: str) -> str:
    if battery_status in {"unknown", "no_battery"}:
        return "Unknown"
    if percent > 30:
        return "Healthy"
    if percent > 15:
        return "Low"
    return "Critical"


def get_telemetry_data() -> dict:
    """Return live telemetry or an explicit disconnected payload."""
    diagnostics = drone.get_diagnostics()

    if diagnostics["connected"]:
        try:
            telemetry = drone.get_telemetry()
            battery = telemetry.get("battery", {})
            percent = float(battery.get("percent", 0.0) or 0.0)
            voltage = float(battery.get("voltage", 0.0) or 0.0)
            current = float(battery.get("current", 0.0) or 0.0)
            consumed = int(battery.get("consumedMah", 0) or 0)
            capacity_remaining = float(
                battery.get("capacityRemaining", 0.0) or 0.0
            )
            battery_status = str(battery.get("status", "unknown"))
            time_left = float(battery.get("flightTimeMinutes", 0.0) or 0.0)

            last_update = (
                drone.last_update.isoformat()
                if drone.last_update is not None
                else ""
            )
            packet_age = diagnostics["packetAge"]
            quality = (
                "Live"
                if packet_age is not None
                and packet_age <= STALE_AFTER_SECONDS
                else "Stale"
            )
            heartbeat_status = diagnostics["heartbeatStatus"]

            status_data = telemetry.get("status", {})
            armed = bool(status_data.get("armed", False))
            flight_mode = str(status_data.get("mode", "Unknown"))

            return {
                "droneId": DRONE_ID,
                "connectionStatus": "Connected",
                "armed": armed,
                "flightMode": flight_mode,
                "missionStatus": "En Route" if armed else "Idle",
                "connectionType": diagnostics["transport"],
                "position": telemetry.get(
                    "position",
                    DISCONNECTED_TELEMETRY_TEMPLATE["position"],
                ),
                "navigation": telemetry.get(
                    "navigation",
                    DISCONNECTED_TELEMETRY_TEMPLATE["navigation"],
                ),
                "attitude": telemetry.get(
                    "attitude",
                    DISCONNECTED_TELEMETRY_TEMPLATE["attitude"],
                ),
                "gps": telemetry.get(
                    "gps",
                    DISCONNECTED_TELEMETRY_TEMPLATE["gps"],
                ),
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
                "charging": charging_telemetry.model_dump(),
                "communication": {
                    "rssi": telemetry.get("communication", {}).get("rssi", 0),
                    "linkStatus": diagnostics["transport"],
                    "packetLoss": telemetry.get("communication", {}).get(
                        "packetLoss",
                        0.0,
                    ),
                    "dataAge": packet_age,
                    "quality": quality,
                    "heartbeatStatus": heartbeat_status,
                    "heartbeatAge": diagnostics["heartbeatAge"],
                    "lastMessageType": diagnostics["lastMessageType"],
                    "packetCount": diagnostics["packetCount"],
                    "badDataCount": diagnostics["badDataCount"],
                    "lastUpdate": last_update,
                },
            }
        except Exception as exc:
            print(f"Error constructing telemetry payload: {exc}")

    disconnected = deepcopy(DISCONNECTED_TELEMETRY_TEMPLATE)
    disconnected["charging"] = charging_telemetry.model_dump()
    disconnected["communication"].update(
        {
            "heartbeatStatus": diagnostics["heartbeatStatus"],
            "heartbeatAge": diagnostics["heartbeatAge"],
            "lastMessageType": diagnostics["lastMessageType"],
            "packetCount": diagnostics["packetCount"],
            "badDataCount": diagnostics["badDataCount"],
            "lastUpdate": datetime.now().isoformat(),
        }
    )
    return disconnected


async def reconnect_drone_task() -> None:
    """Rotate through configured endpoints until live MAVLink traffic arrives."""
    print(f"MAVLink monitoring active. Targets: {', '.join(CONNECTION_TARGETS)}")
    print(
        "Initial connection requirement: "
        + ("HEARTBEAT" if REQUIRE_HEARTBEAT else "any valid MAVLink packet")
    )
    target_index = 0

    while True:
        try:
            if not drone.is_connected_to_drone():
                target = CONNECTION_TARGETS[target_index]
                target_index = (target_index + 1) % len(CONNECTION_TARGETS)
                print(f"Connection inactive. Trying: {target}")

                connected = await asyncio.to_thread(
                    drone.connect,
                    target,
                    INITIAL_PACKET_TIMEOUT,
                )
                if connected:
                    print(f"MAVLink traffic established on {target}")
                else:
                    print(
                        f"No acceptable MAVLink traffic on {target}; "
                        f"retrying in {RECONNECT_DELAY_SECONDS:g}s"
                    )

            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Reconnect worker error: {exc}")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)


async def update_telemetry_loop() -> None:
    """Refresh the in-memory API payload at 20 Hz."""
    global latest_telemetry
    while True:
        try:
            latest_telemetry = get_telemetry_data()
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Telemetry update loop error: {exc}")
            await asyncio.sleep(0.2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    print("Starting MAVLink backend workers")
    reconnect_task = asyncio.create_task(reconnect_drone_task())
    update_task = asyncio.create_task(update_telemetry_loop())
    try:
        yield
    finally:
        reconnect_task.cancel()
        update_task.cancel()
        await asyncio.gather(
            reconnect_task,
            update_task,
            return_exceptions=True,
        )
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


@app.put("/api/charging", dependencies=[Depends(require_api_key)])
async def update_charging(payload: ChargingTelemetry) -> dict:
    global charging_telemetry
    charging_telemetry = payload
    return {
        "status": "updated",
        "charging": payload.model_dump(),
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )