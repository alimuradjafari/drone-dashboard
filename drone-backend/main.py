from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
import uvicorn
import json
from datetime import datetime
import asyncio
import time
from copy import deepcopy
from drone_connection import DroneConnection
from config import API_KEY, BATTERY_CAPACITY_MAH, CONNECTION_TARGETS, CORS_ORIGINS, DRONE_ID, HEARTBEAT_TIMEOUT, STALE_AFTER_SECONDS


def require_api_key(x_api_key: str | None = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

#  LIFESPAN MANAGMENT: Replaces deprecated startup events cleanly
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(" Initializing async background worker loops...")
    reconnect_task = asyncio.create_task(reconnect_drone_task())
    update_task = asyncio.create_task(update_telemetry_loop())
    yield
    # Clean up tasks on shutdown
    reconnect_task.cancel()
    update_task.cancel()
    await asyncio.gather(reconnect_task, update_task, return_exceptions=True)
    await asyncio.to_thread(drone.disconnect)

app = FastAPI(title="Drone Telemetry API (Hybrid Setup)", lifespan=lifespan)

# CORS MIDDLEWARE
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# INITIALIZE DRONE CONNECTION INTERFACE
drone = DroneConnection()

# Direct USB Serial connection
CONNECTION_TARGET = CONNECTION_TARGETS[0]

# STATIC TEMPLATE: Used when the drone is disconnected
DISCONNECTED_TELEMETRY_TEMPLATE = {
    "droneId": DRONE_ID,
    "connectionStatus": "Disconnected",
    "armed": False,
    "flightMode": "Unknown",
    "missionStatus": "Unknown",
    "position": {"lat": 0.0, "lng": 0.0, "alt": 0.0, "heading": 0.0},
    "navigation": {"groundSpeed": 0.0, "distanceFromHome": 0.0},
    "attitude": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
    "gps": {"fixType": "No Fix", "satellites": 0, "hdop": 0.0},
    "battery": {
        "percent": 0, "voltage": 0.0, "current": 0.0,
        "capacity": 0, "timeLeft": 0, "health": "Unknown"
    },
    "charging": {
        "docked": False, "status": "Unknown", "progress": 0.0,
        "voltage": 0.0, "current": 0.0, "eta": 0.0
    },
    "communication": {
        "rssi": 0.0, "linkStatus": "Disconnected", "packetLoss": 0.0, "lastUpdate": ""
    }
}


class ChargingTelemetry(BaseModel):
    docked: bool = False
    status: str = "Unknown"
    progress: float = Field(default=0, ge=0, le=100)
    voltage: float = Field(default=0, ge=0)
    current: float = Field(default=0, ge=0)
    eta: float = Field(default=0, ge=0)


charging_telemetry = ChargingTelemetry()

# Ensure global state starts safe
latest_telemetry = deepcopy(DISCONNECTED_TELEMETRY_TEMPLATE)


def get_telemetry_data():
    """Get live data from drone. If drone is offline, return explicit disconnected state."""
    now = time.time()
    thread_heartbeat = getattr(drone, 'last_heartbeat_time', 0)
    time_since_last_heartbeat = now - thread_heartbeat

    # Watchdog Check
    if time_since_last_heartbeat > 4.0 and drone.is_connected_to_drone():
        print(f" WATCHDOG TRIGGERED: Lost heartbeat connection for {time_since_last_heartbeat:.1f}s. Forcing offline state.")
        drone.is_connected = False  

    if drone.is_connected_to_drone():
        try:
            telemetry = drone.get_telemetry()
            percent = telemetry["battery"]["percent"]
            consumed = telemetry["battery"].get("consumedMah")
            if BATTERY_CAPACITY_MAH and consumed is not None:
                capacity_left = max(0, BATTERY_CAPACITY_MAH - consumed)
            else:
                capacity_left = round(BATTERY_CAPACITY_MAH * percent / 100)
            current = telemetry["battery"]["current"]
            time_left = round((capacity_left / 1000) / current * 60) if capacity_left and current > 0 else 0
            data_age = max(0.0, time.time() - drone.last_heartbeat_time)
            return {
                "droneId": DRONE_ID,
                "connectionStatus": "Connected",
                "armed": telemetry["status"]["armed"],
                "flightMode": telemetry["status"]["mode"],
                "missionStatus": "En Route" if telemetry["status"]["armed"] else "Idle",
                "position": telemetry["position"],
                "navigation": {
                    "groundSpeed": telemetry["navigation"]["groundSpeed"],
                    "distanceFromHome": telemetry["navigation"].get("distanceFromHome", 0.0)
                },
                "attitude": telemetry["attitude"],
                "gps": telemetry["gps"],
                "battery": {
                    "percent": telemetry["battery"]["percent"],
                    "voltage": telemetry["battery"]["voltage"],
                    "current": telemetry["battery"]["current"],
                    "capacity": capacity_left,
                    "timeLeft": time_left,
                    "health": "Healthy" if telemetry["battery"]["percent"] > 20 else "Critical"
                },
                "charging": charging_telemetry.model_dump(),
                "communication": {
                    "rssi": telemetry["communication"]["rssi"],
                    "linkStatus": drone.transport,
                    "packetLoss": telemetry["communication"]["packetLoss"],
                    "transport": drone.transport,
                    "dataAge": round(data_age, 2),
                    "quality": "Stale" if data_age > STALE_AFTER_SECONDS else "Live",
                    "lastUpdate": datetime.now().isoformat()
                }
            }
        except Exception as e:
            print(f" Error extracting telemetry dictionary: {e}")
            #  FIXED: Instantly short-circuit here if data parsing fails
            
    disconnected_data = deepcopy(DISCONNECTED_TELEMETRY_TEMPLATE)
    disconnected_data["charging"] = charging_telemetry.model_dump()
    disconnected_data["communication"]["lastUpdate"] = datetime.now().isoformat()
    return disconnected_data


# BACKGROUND RECONNECTION TASK
async def reconnect_drone_task():
    """Continuously monitors connection state and executes hot-reconnect attempts safely"""
    print(f"Hardware monitoring active. Targets: {', '.join(CONNECTION_TARGETS)}")
    target_index = 0
    while True:
        if not drone.is_connected_to_drone():
            target = CONNECTION_TARGETS[target_index]
            target_index = (target_index + 1) % len(CONNECTION_TARGETS)
            print(f"Connection inactive. Trying: {target}")
            try:
                connected = await asyncio.to_thread(drone.connect, target, HEARTBEAT_TIMEOUT)
                if connected:
                    print(" Successfully established connection handle!")
                else:
                    print("❌ Connection attempt did not establish a heartbeat. Retrying in 3 seconds...")
            except Exception as e:
                print(f"❌ Connection attempt failed: {e}. Retrying in 3 seconds...")
        
        await asyncio.sleep(3)


# BACKGROUND UPDATE LOOP (Throttled to 20Hz)
async def update_telemetry_loop():
    """Background task that pulls telemetry updates from the drone instance at 20Hz"""
    global latest_telemetry
    while True:
        latest_telemetry = get_telemetry_data()
        await asyncio.sleep(0.05)


# WEBSOCKET CONNECTIONS STORE
active_connections = []

@app.websocket("/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(default=None)):
    if API_KEY and token != API_KEY:
        await websocket.close(code=1008, reason="Invalid API key")
        return
    await websocket.accept()
    active_connections.append(websocket)
    print(f" Client connected to UI pipe. Total active dashboard clients: {len(active_connections)}")
    
    try:
        # Send initial immediate payload snapshot
        await websocket.send_text(json.dumps(latest_telemetry))
        while True:
            await asyncio.sleep(0.1)  # 10Hz UI dashboard feed rate
            await websocket.send_text(json.dumps(latest_telemetry))
            
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        print(f" Client disconnected from UI pipe. Total remaining clients: {len(active_connections)}")


@app.get("/")
async def root():
    return {"status": "online", "message": "Drone Telemetry API is running"}


@app.get("/api/telemetry/latest")
async def get_latest_telemetry(_: None = Depends(require_api_key)):
    return latest_telemetry


@app.get("/api/status", dependencies=[Depends(require_api_key)])
async def get_status():
    age = max(0.0, time.time() - drone.last_heartbeat_time) if drone.last_heartbeat_time else None
    return {
        "connected": drone.is_connected_to_drone(),
        "transport": drone.transport,
        "connection": drone.connection_string,
        "heartbeatAge": round(age, 2) if age is not None else None,
        "configuredTargets": CONNECTION_TARGETS,
    }


@app.put("/api/charging", dependencies=[Depends(require_api_key)])
async def update_charging(payload: ChargingTelemetry):
    global charging_telemetry
    charging_telemetry = payload
    return {"status": "updated", "charging": payload}


if __name__ == "__main__":
    # Note: main:app targets main.py file name string dynamically
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
