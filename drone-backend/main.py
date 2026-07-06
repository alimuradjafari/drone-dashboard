from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
from datetime import datetime
import asyncio
import time
from drone_connection import DroneConnection

app = FastAPI(title="Drone Telemetry API (Hybrid Setup)")

# CORS MIDDLEWARE
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# INITIALIZE DRONE CONNECTION INTERFACE
drone = DroneConnection()

# Direct USB Serial connection
CONNECTION_TARGET = 'COM9'

# GLOBAL TELEMETRY STORAGE INITIALIZATION
latest_telemetry = {}

# STATIC TEMPLATE: Used when the drone is disconnected or when read threads fail
DISCONNECTED_TELEMETRY_TEMPLATE = {
    "droneId": "Drone-001",
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

# Ensure global state starts safe before tasks kick off
latest_telemetry = DISCONNECTED_TELEMETRY_TEMPLATE.copy()


def get_telemetry_data():
    """Get live data from drone. If drone is offline, return explicit disconnected state."""
    now = time.time()
    thread_heartbeat = getattr(drone, 'last_heartbeat_time', 0)
    time_since_last_heartbeat = now - thread_heartbeat

    # Watchdog Check
    if time_since_last_heartbeat > 4.0 and drone.is_connected_to_drone():
        print(f"⚠️ WATCHDOG TRIGGERED: Lost heartbeat connection for {time_since_last_heartbeat:.1f}s. Forcing offline state.")
        drone.is_connected = False  

    if drone.is_connected_to_drone():
        try:
            telemetry = drone.get_telemetry()
            is_udp = "udpin" in CONNECTION_TARGET
            rssi_value = -55 if is_udp else -45
            
            return {
                "droneId": "Drone-001",
                "connectionStatus": "Connected",
                "armed": telemetry["status"]["armed"],
                "flightMode": telemetry["status"]["mode"],
                "missionStatus": "En Route" if telemetry["status"]["armed"] else "Idle",
                "position": telemetry["position"],
                "navigation": {
                    "groundSpeed": telemetry["navigation"]["groundSpeed"],
                    "distanceFromHome": 0.0 
                },
                "attitude": telemetry["attitude"],
                "gps": telemetry["gps"],
                "battery": {
                    "percent": telemetry["battery"]["percent"],
                    "voltage": telemetry["battery"]["voltage"],
                    "current": telemetry["battery"]["current"],
                    "capacity": 0,
                    "timeLeft": 0,
                    "health": "Healthy" if telemetry["battery"]["percent"] > 20 else "Critical"
                },
                "charging": {
                    "docked": False, "status": "Not Charging", "progress": 0,
                    "voltage": 0, "current": 0, "eta": 0
                },
                "communication": {
                    "rssi": rssi_value,
                    "linkStatus": "Connected",
                    "packetLoss": 0.2 if not is_udp else 0.5,
                    "lastUpdate": datetime.now().isoformat()
                }
            }
        except Exception as e:
            print(f"❌ Error extracting telemetry dictionary: {e}")
            
    disconnected_data = DISCONNECTED_TELEMETRY_TEMPLATE.copy()
    disconnected_data["communication"]["lastUpdate"] = datetime.now().isoformat()
    return disconnected_data


# BACKGROUND RECONNECTION TASK
async def reconnect_drone_task():
    """Continuously monitors connection state and executes hot-reconnect attempts if target drops"""
    print(f"🔌 Hardware monitoring loop active. Target profile: {CONNECTION_TARGET}")
    while True:
        if not drone.is_connected_to_drone():
            print(f"⚡ Connection broken/inactive. Instantiating hook to: {CONNECTION_TARGET}...")
            try:
                drone.connect(CONNECTION_TARGET)
                print("✅ Successfully established connection handle!")
            except Exception as e:
                print(f"❌ Connection attempt failed: {e}. Retrying in 3 seconds...")
        
        await asyncio.sleep(3)


# BACKGROUND UPDATE LOOP (Throttled to 20Hz for Real-time Hardware Extraction)
async def update_telemetry_loop():
    """Background task that pulls telemetry updates from the drone instance at 20Hz"""
    global latest_telemetry
    while True:
        latest_telemetry = get_telemetry_data()
        await asyncio.sleep(0.05)


# WEBSOCKET CONNECTIONS STORE
active_connections = []

@app.websocket("/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    print(f"🚀 Client connected to UI pipe. Total active dashboard clients: {len(active_connections)}")
    
    try:
        await websocket.send_text(json.dumps(latest_telemetry))
        while True:
            await asyncio.sleep(0.1)
            await websocket.send_text(json.dumps(latest_telemetry))
            
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        print(f"🛑 Client disconnected from UI pipe. Total remaining clients: {len(active_connections)}")


@app.get("/")
async def root():
    return {"status": "online", "message": "Drone Telemetry API is running"}


@app.get("/api/telemetry/latest")
async def get_latest_telemetry():
    return latest_telemetry


# STARTUP EVENT - Register background tasks cleanly
@app.on_event("startup")
async def startup_event():
    print("⚙️ Initializing async background worker loops...")
    asyncio.create_task(reconnect_drone_task())
    asyncio.create_task(update_telemetry_loop())


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)