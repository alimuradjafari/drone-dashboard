# main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
from datetime import datetime
import asyncio
import random
from drone_connection import DroneConnection

# ============================================================
# 1. CREATE FASTAPI APP
# ============================================================
app = FastAPI(title="Drone Telemetry API", version="1.0.0")

# ============================================================
# 2. ADD CORS MIDDLEWARE
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 3. INITIALIZE DRONE CONNECTION
# ============================================================
drone = DroneConnection()
try:
    drone.connect('COM6')
    print(" Connected to drone on COM6")
except Exception as e:
    print(f" Could not connect to drone: {e}")
    print("ℹ Using mock data instead")


# ============================================================
# 4. HELPER FUNCTION: Create static mock telemetry data
# ============================================================
def get_mock_telemetry():
    """Return static mock telemetry data for testing"""
    return {
        "droneId": "Drone-001",
        "connectionStatus": "Connected",
        "armed": False,
        "flightMode": "Stabilize",
        "missionStatus": "Idle",
        "position": {
            "lat": 37.7749,
            "lng": -122.4194,
            "alt": 42.5,
            "heading": 127.3
        },
        "navigation": {
            "groundSpeed": 8.4,
            "distanceFromHome": 312.0
        },
        "attitude": {
            "roll": 2.1,
            "pitch": -5.3,
            "yaw": 127.3
        },
        "gps": {
            "fixType": "3D Fix",
            "satellites": 14,
            "hdop": 0.82
        },
        "battery": {
            "percent": 72,
            "voltage": 22.4,
            "current": 18.6,
            "capacity": 3240,
            "timeLeft": 11,
            "health": "Healthy"
        },
        "charging": {
            "docked": False,
            "status": "Standby",
            "progress": 62.0,
            "voltage": 24.6,
            "current": 4.2,
            "eta": 18.0
        },
        "communication": {
            "rssi": -68.0,
            "linkStatus": "Active",
            "packetLoss": 0.4,
            "lastUpdate": datetime.now().isoformat()
        }
    }


# ============================================================
# 5. HELPER FUNCTION: Create changing mock telemetry data
# ============================================================
def get_changing_mock_data():
    """Return mock data with values that change over time"""
    
    # Get the base mock data
    data = get_mock_telemetry()
    
    # Change values to simulate real-time updates
    # Position changes slightly (random walk)
    data["position"]["lat"] += (random.random() - 0.5) * 0.0001
    data["position"]["lng"] += (random.random() - 0.5) * 0.0001
    data["position"]["alt"] = max(0, data["position"]["alt"] + (random.random() - 0.5) * 2)
    
    # Heading changes slightly
    data["position"]["heading"] = (data["position"]["heading"] + (random.random() - 0.5) * 5) % 360
    
    # Battery slowly decreases (but never below 5%)
    current_battery = data["battery"]["percent"]
    data["battery"]["percent"] = max(5, current_battery - random.random() * 0.5)
    data["battery"]["percent"] = round(data["battery"]["percent"], 1)
    
    # Update voltage based on battery level
    data["battery"]["voltage"] = round(22.4 - (72 - data["battery"]["percent"]) * 0.05, 1)
    
    # Update flight time based on battery
    data["battery"]["timeLeft"] = round(data["battery"]["percent"] * 0.15, 0)
    
    # Update battery health
    if data["battery"]["percent"] > 40:
        data["battery"]["health"] = "Healthy"
    elif data["battery"]["percent"] > 20:
        data["battery"]["health"] = "Low"
    else:
        data["battery"]["health"] = "Critical"
    
    # Update timestamp
    data["communication"]["lastUpdate"] = datetime.now().isoformat()
    
    # Randomly change flight mode occasionally
    if random.random() < 0.02:  # 2% chance
        modes = ["Stabilize", "Loiter", "Auto", "RTL", "Guided"]
        data["flightMode"] = random.choice(modes)
    
    return data


# ============================================================
# 6. HELPER FUNCTION: Get telemetry (drone or mock)
# ============================================================
def get_telemetry_data():
    """Get telemetry from drone or fallback to mock"""
    
    # If connected to drone, use real data
    if drone.is_connected_to_drone():
        telemetry = drone.get_telemetry()
        
        # Format to match our dashboard structure
        return {
            "droneId": "Drone-001",
            "connectionStatus": "Connected",
            "armed": telemetry["status"]["armed"],
            "flightMode": telemetry["status"]["mode"],
            "missionStatus": "En Route" if telemetry["status"]["armed"] else "Idle",
            "position": telemetry["position"],
            "navigation": {
                "groundSpeed": telemetry["navigation"]["groundSpeed"],
                "distanceFromHome": 312.0  # Not available from MAVLink
            },
            "attitude": telemetry["attitude"],
            "gps": telemetry["gps"],
            "battery": {
                "percent": telemetry["battery"]["percent"],
                "voltage": telemetry["battery"]["voltage"],
                "current": telemetry["battery"]["current"],
                "capacity": 5200,  # Default
                "timeLeft": 25,     # Calculate from battery
                "health": "Healthy" if telemetry["battery"]["percent"] > 20 else "Critical"
            },
            "charging": {
                "docked": False,
                "status": "Not Charging",
                "progress": 0,
                "voltage": 0,
                "current": 0,
                "eta": 0
            },
            "communication": {
                "rssi": -45,  # Not available from MAVLink
                "linkStatus": "Connected",
                "packetLoss": 0.2,
                "lastUpdate": datetime.now().isoformat()
            }
        }
    
    # Fallback to changing mock data
    return get_changing_mock_data()


# ============================================================
# 7. INITIAL TELEMETRY STATE
# ============================================================
latest_telemetry = get_telemetry_data()


# ============================================================
# 8. BACKGROUND UPDATE LOOP
# ============================================================
async def update_telemetry_loop():
    """Background task that updates telemetry every 2 seconds"""
    global latest_telemetry
    print(" Started telemetry update loop (every 2 seconds)")
    while True:
        latest_telemetry = get_telemetry_data()
        await asyncio.sleep(2)


# ============================================================
# 9. WEBSOCKET CONNECTIONS STORE
# ============================================================
active_connections = []  # List of WebSocket connections


# ============================================================
# 10. WEBSOCKET ENDPOINT
# ============================================================
@app.websocket("/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time telemetry"""
    
    await websocket.accept()
    active_connections.append(websocket)
    print(f" Client connected. Total clients: {len(active_connections)}")
    
    try:
        # Send initial data immediately (from shared state)
        await websocket.send_text(json.dumps(latest_telemetry))
        
        # Keep connection alive and send updates
        while True:
            await asyncio.sleep(2)
            # Send the latest telemetry from shared state
            await websocket.send_text(json.dumps(latest_telemetry))
            
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        print(f" Client disconnected. Total clients: {len(active_connections)}")


# ============================================================
# 11. REST API ENDPOINTS
# ============================================================
@app.get("/")
async def root():
    return {
        "status": "online",
        "message": "Drone Telemetry API is running",
        "version": "1.0.0"
    }


@app.get("/api/telemetry/latest")
async def get_latest_telemetry():
    """Get the latest telemetry data"""
    return latest_telemetry


@app.get("/api/telemetry/mock")
def get_mock_endpoint():
    """Get mock telemetry data (for testing)"""
    return get_mock_telemetry()


# ============================================================
# 12. STARTUP EVENT - Start background tasks
# ============================================================
@app.on_event("startup")
async def startup_event():
    """Run when the server starts"""
    print(" Starting background telemetry update loop...")
    asyncio.create_task(update_telemetry_loop())


# ============================================================
# 13. RUN THE SERVER
# ============================================================
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )