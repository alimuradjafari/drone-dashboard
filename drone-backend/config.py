"""Application configuration loaded from the .env file beside this module."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ENV_FILE = Path(__file__).with_name(".env")
load_dotenv(dotenv_path=ENV_FILE)


def csv_setting(name: str, default: str) -> list[str]:
    """Return a comma-separated environment setting as a clean list."""
    return [
        item.strip()
        for item in os.getenv(name, default).split(",")
        if item.strip()
    ]


def bool_setting(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false, yes/no, on/off, or 1/0")


def float_setting(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def int_setting(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


DRONE_ID = os.getenv("DRONE_ID", "Drone-001").strip() or "Drone-001"
CONNECTION_TARGETS = csv_setting(
    "MAVLINK_CONNECTIONS",
    "udpin:0.0.0.0:14550",
)
if not CONNECTION_TARGETS:
    raise ValueError("MAVLINK_CONNECTIONS must contain at least one target")

# Backward compatibility: the previous project used MAVLINK_HEARTBEAT_TIMEOUT
# for the initial connection wait. The new name describes the behavior better.
_initial_timeout_default = float_setting("MAVLINK_HEARTBEAT_TIMEOUT", 10.0)
INITIAL_PACKET_TIMEOUT = float_setting(
    "MAVLINK_INITIAL_PACKET_TIMEOUT",
    _initial_timeout_default,
)
HEARTBEAT_TIMEOUT = INITIAL_PACKET_TIMEOUT

REQUIRE_HEARTBEAT = bool_setting("MAVLINK_REQUIRE_HEARTBEAT", False)
STALE_AFTER_SECONDS = float_setting("TELEMETRY_STALE_AFTER", 8.0)
HEARTBEAT_STALE_AFTER_SECONDS = float_setting(
    "MAVLINK_HEARTBEAT_STALE_AFTER",
    5.0,
)
REQUEST_STREAM_RATE_HZ = int_setting("MAVLINK_REQUEST_STREAM_RATE_HZ", 20)
RECONNECT_DELAY_SECONDS = float_setting("MAVLINK_RECONNECT_DELAY", 3.0)
MAVLINK_SOURCE_SYSTEM = int_setting("MAVLINK_SOURCE_SYSTEM", 255)
MAVLINK_SOURCE_COMPONENT = int_setting("MAVLINK_SOURCE_COMPONENT", 190)

CORS_ORIGINS = csv_setting(
    "CORS_ORIGINS",
    "http://localhost:5500,http://127.0.0.1:5500",
)
API_KEY = os.getenv("DASHBOARD_API_KEY", "")
BATTERY_CAPACITY_MAH = max(0, int_setting("BATTERY_CAPACITY_MAH", 5000))

if INITIAL_PACKET_TIMEOUT <= 0:
    raise ValueError("MAVLINK_INITIAL_PACKET_TIMEOUT must be greater than zero")
if STALE_AFTER_SECONDS <= 0:
    raise ValueError("TELEMETRY_STALE_AFTER must be greater than zero")
if HEARTBEAT_STALE_AFTER_SECONDS <= 0:
    raise ValueError("MAVLINK_HEARTBEAT_STALE_AFTER must be greater than zero")
if RECONNECT_DELAY_SECONDS <= 0:
    raise ValueError("MAVLINK_RECONNECT_DELAY must be greater than zero")
if not 1 <= MAVLINK_SOURCE_SYSTEM <= 255:
    raise ValueError("MAVLINK_SOURCE_SYSTEM must be between 1 and 255")
if not 1 <= MAVLINK_SOURCE_COMPONENT <= 255:
    raise ValueError("MAVLINK_SOURCE_COMPONENT must be between 1 and 255")