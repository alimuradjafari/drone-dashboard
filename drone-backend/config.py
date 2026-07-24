import os

from dotenv import load_dotenv

load_dotenv()


def csv_setting(name, default):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DRONE_ID = os.getenv("DRONE_ID", "Drone-001")
CONNECTION_TARGETS = csv_setting("MAVLINK_CONNECTIONS", "udp:192.168.137.70:14550,COM9")
HEARTBEAT_TIMEOUT = float(os.getenv("MAVLINK_HEARTBEAT_TIMEOUT", "5"))
CORS_ORIGINS = csv_setting("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500")
API_KEY = os.getenv("DASHBOARD_API_KEY", "")
BATTERY_CAPACITY_MAH = int(os.getenv("BATTERY_CAPACITY_MAH", "0"))
STALE_AFTER_SECONDS = float(os.getenv("TELEMETRY_STALE_AFTER", "4"))
