"""Threaded MAVLink receiver used by the telemetry API.

The transport becomes connected after the first valid MAVLink packet unless
``require_heartbeat`` is enabled. HEARTBEAT health is tracked separately so
telemetry can still be inspected when routing accidentally omits heartbeat.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from math import atan2, cos, degrees, radians, sin, sqrt
import threading
import time
from typing import Any

from pymavlink import mavutil
import serial


class DroneConnection:
    def __init__(
        self,
        *,
        stale_after_seconds: float = 8.0,
        heartbeat_stale_after_seconds: float = 5.0,
        require_heartbeat: bool = False,
        battery_capacity_mah: int = 5000,
        request_stream_rate_hz: int = 20,
        source_system: int = 255,
        source_component: int = 190,
    ) -> None:
        self.stale_after_seconds = float(stale_after_seconds)
        self.heartbeat_stale_after_seconds = float(heartbeat_stale_after_seconds)
        self.require_heartbeat = bool(require_heartbeat)
        self.battery_capacity_mah = max(0, int(battery_capacity_mah))
        self.request_stream_rate_hz = max(0, int(request_stream_rate_hz))
        self.source_system = int(source_system)
        self.source_component = int(source_component)

        self.mav: Any | None = None
        self.is_connected = False
        self.running = False
        self.thread: threading.Thread | None = None
        self.connection_string: str | None = None
        self.transport = "disconnected"
        self.home_position: tuple[float, float] | None = None

        self.last_packet_time = 0.0
        self.last_heartbeat_time = 0.0
        self.last_update: datetime | None = None
        self.last_message_type: str | None = None
        self.packet_count = 0
        self.bad_data_count = 0
        self.last_error: str | None = None

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._first_packet_event = threading.Event()
        self._heartbeat_event = threading.Event()
        self._stream_requested = False

        self.telemetry = self._new_telemetry()

    def _new_telemetry(self) -> dict[str, Any]:
        capacity = self.battery_capacity_mah
        return {
            "position": {
                "lat": 0.0,
                "lng": 0.0,
                "alt": 0.0,
                "heading": 0.0,
            },
            "battery": {
                "voltage": 0.0,
                "current": 0.0,
                "percent": 0.0,
                "consumedMah": 0,
                "capacityRemaining": 0,
                "capacityFull": capacity,
                "flightTimeMinutes": 0.0,
                "status": "unknown",
            },
            "status": {
                "armed": False,
                "mode": "Unknown",
            },
            "gps": {
                "fixType": "No GPS",
                "satellites": 0,
                "hdop": 0.0,
            },
            "attitude": {
                "roll": 0.0,
                "pitch": 0.0,
                "yaw": 0.0,
            },
            "navigation": {
                "groundSpeed": 0.0,
                "airSpeed": 0.0,
                "distanceFromHome": 0.0,
                "distanceToWaypoint": 0.0,
            },
            "communication": {
                "rssi": 0,
                "packetLoss": 0.0,
            },
        }

    def connect(
        self,
        connection_string: str = "udpin:0.0.0.0:14550",
        initial_timeout: float = 10.0,
    ) -> bool:
        """Open a MAVLink endpoint and wait for usable traffic.

        By default, any valid MAVLink message establishes the transport. If
        ``require_heartbeat`` was enabled at construction, only HEARTBEAT will
        satisfy the initial wait.
        """
        self.disconnect(quiet=True)
        print(f"Opening MAVLink endpoint: {connection_string}")

        try:
            mav = mavutil.mavlink_connection(
                connection_string,
                source_system=self.source_system,
                source_component=self.source_component,
                autoreconnect=False,
            )
        except Exception as exc:
            self.last_error = str(exc)
            print(f"MAVLink endpoint open failed: {exc}")
            return False

        with self._lock:
            self.mav = mav
            self.connection_string = connection_string
            self.transport = self._get_connection_type(connection_string)
            self.running = True
            self.is_connected = False
            self.last_packet_time = 0.0
            self.last_heartbeat_time = 0.0
            self.last_update = None
            self.last_message_type = None
            self.packet_count = 0
            self.bad_data_count = 0
            self.last_error = None
            self._stream_requested = False
            self.telemetry = self._new_telemetry()

        self._stop_event.clear()
        self._first_packet_event.clear()
        self._heartbeat_event.clear()
        self.start_reading()

        ready_event = (
            self._heartbeat_event
            if self.require_heartbeat
            else self._first_packet_event
        )
        requirement = "HEARTBEAT" if self.require_heartbeat else "valid MAVLink packet"
        print(f"Waiting up to {initial_timeout:g}s for {requirement}...")

        if not ready_event.wait(timeout=initial_timeout):
            details = self.last_error or f"No {requirement} received"
            print(f"MAVLink connection not established: {details}")
            self.disconnect(quiet=True)
            return False

        if not self.is_connected_to_drone():
            print("MAVLink traffic arrived but became stale during connection setup")
            self.disconnect(quiet=True)
            return False

        if self.last_heartbeat_time:
            print(
                "MAVLink connected with heartbeat "
                f"(system={getattr(self.mav, 'target_system', 0)}, "
                f"component={getattr(self.mav, 'target_component', 0)})"
            )
        else:
            print(
                "MAVLink transport connected from live packets; "
                "HEARTBEAT has not been received"
            )
        return True

    def start_reading(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self._read_telemetry_loop,
            name="mavlink-reader",
            daemon=True,
        )
        self.thread.start()
        print("Started MAVLink telemetry reader thread")

    def _read_telemetry_loop(self) -> None:
        while not self._stop_event.is_set():
            mav = self.mav
            if mav is None:
                break

            try:
                message = mav.recv_match(blocking=True, timeout=0.5)
            except (serial.SerialException, OSError, AttributeError) as exc:
                self._set_reader_error(f"Physical connection error: {exc}")
                break
            except Exception as exc:
                self._set_reader_error(f"MAVLink receive error: {exc}")
                time.sleep(0.1)
                continue

            if message is None:
                if self._packet_stream_is_stale():
                    self._set_reader_error(
                        "No valid MAVLink packets within telemetry stale timeout"
                    )
                    break
                continue

            message_type = message.get_type()
            if message_type == "BAD_DATA":
                with self._lock:
                    self.bad_data_count += 1
                continue

            now = time.time()
            with self._lock:
                self.last_packet_time = now
                self.last_update = datetime.now()
                self.last_message_type = message_type
                self.packet_count += 1
                self.is_connected = True
            self._first_packet_event.set()

            try:
                self._dispatch_message(message_type, message, now)
            except Exception as exc:
                # A malformed individual message should not kill the transport.
                self.last_error = f"Failed to process {message_type}: {exc}"
                print(self.last_error)

        with self._lock:
            self.running = False
            self.is_connected = False

    def _dispatch_message(self, message_type: str, message: Any, now: float) -> None:
        if message_type == "HEARTBEAT":
            with self._lock:
                self.last_heartbeat_time = now
            self._heartbeat_event.set()
            self._process_heartbeat(message)
            self._request_stream_once()
        elif message_type == "GLOBAL_POSITION_INT":
            self._process_position(message)
        elif message_type in {"SYS_STATUS", "BATTERY_STATUS"}:
            self._process_battery(message)
        elif message_type == "NAV_CONTROLLER_OUTPUT":
            self._process_nav_output(message)
        elif message_type == "GPS_RAW_INT":
            self._process_gps(message)
        elif message_type == "ATTITUDE":
            self._process_attitude(message)
        elif message_type == "VFR_HUD":
            self._process_vfr(message)
        elif message_type in {"RADIO_STATUS", "RADIO"}:
            self._process_radio(message)
        elif message_type == "HOME_POSITION":
            self._process_home_position(message)

    def _request_stream_once(self) -> None:
        if self._stream_requested or self.request_stream_rate_hz <= 0:
            return
        mav = self.mav
        if mav is None:
            return

        target_system = int(getattr(mav, "target_system", 0) or 0)
        target_component = int(getattr(mav, "target_component", 0) or 0)
        if target_system <= 0:
            return

        try:
            mav.mav.request_data_stream_send(
                target_system,
                target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL,
                self.request_stream_rate_hz,
                1,
            )
            self._stream_requested = True
            print(
                f"Requested MAVLink streams at {self.request_stream_rate_hz} Hz "
                f"from {target_system}:{target_component}"
            )
        except Exception as exc:
            # UDP forwarding may be receive-only. Telemetry can still continue.
            print(f"Could not request stream rates (non-fatal): {exc}")

    def _packet_stream_is_stale(self) -> bool:
        with self._lock:
            last_packet_time = self.last_packet_time
        return bool(
            last_packet_time
            and time.time() - last_packet_time > self.stale_after_seconds
        )

    def _set_reader_error(self, message: str) -> None:
        self.last_error = message
        with self._lock:
            self.is_connected = False
        print(message)

    def _process_position(self, message: Any) -> None:
        heading_raw = int(getattr(message, "hdg", 65535))
        heading = 0.0 if heading_raw == 65535 else heading_raw / 100.0
        position = {
            "lat": float(message.lat) / 1e7,
            "lng": float(message.lon) / 1e7,
            "alt": float(message.relative_alt) / 1000.0,
            "heading": round(heading, 2),
        }
        with self._lock:
            self.telemetry["position"] = position
            home_position = self.home_position

        if home_position:
            distance = self._distance_metres(
                position["lat"],
                position["lng"],
                home_position[0],
                home_position[1],
            )
            with self._lock:
                self.telemetry["navigation"]["distanceFromHome"] = distance

    def _process_home_position(self, message: Any) -> None:
        home = (
            float(message.latitude) / 1e7,
            float(message.longitude) / 1e7,
        )
        with self._lock:
            self.home_position = home

    @staticmethod
    def _distance_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6_371_000.0
        phi1, phi2 = radians(lat1), radians(lat2)
        d_phi = radians(lat2 - lat1)
        d_lambda = radians(lon2 - lon1)
        a = (
            sin(d_phi / 2) ** 2
            + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
        )
        a = min(1.0, max(0.0, a))
        return round(radius * 2 * atan2(sqrt(a), sqrt(1 - a)), 1)

    def _process_nav_output(self, message: Any) -> None:
        # wp_dist is distance to the active waypoint, not distance from home.
        with self._lock:
            self.telemetry["navigation"]["distanceToWaypoint"] = float(
                getattr(message, "wp_dist", 0.0)
            )

    def _process_battery(self, message: Any) -> None:
        message_type = message.get_type()
        with self._lock:
            previous = deepcopy(self.telemetry.get("battery", {}))

        voltage = float(previous.get("voltage", 0.0) or 0.0)
        current = float(previous.get("current", 0.0) or 0.0)
        percent = float(previous.get("percent", 0.0) or 0.0)
        consumed_mah = int(previous.get("consumedMah", 0) or 0)
        voltage_was_reported = False

        if message_type == "SYS_STATUS":
            raw_voltage = int(getattr(message, "voltage_battery", 65535))
            if 0 < raw_voltage < 65535:
                voltage = raw_voltage / 1000.0
                voltage_was_reported = True

            raw_current = int(getattr(message, "current_battery", -1))
            if raw_current >= 0:
                current = raw_current / 100.0

            raw_percent = int(getattr(message, "battery_remaining", -1))
            if raw_percent >= 0:
                percent = float(raw_percent)

        elif message_type == "BATTERY_STATUS":
            raw_voltages = list(getattr(message, "voltages", []) or [])
            valid_cells = [
                int(cell_mv)
                for cell_mv in raw_voltages
                if cell_mv is not None and 0 < int(cell_mv) < 65535
            ]
            if valid_cells:
                voltage = sum(valid_cells) / 1000.0
                voltage_was_reported = True

            raw_current = int(getattr(message, "current_battery", -1))
            if raw_current >= 0:
                current = raw_current / 100.0

            raw_consumed = int(getattr(message, "current_consumed", -1))
            if raw_consumed >= 0:
                consumed_mah = raw_consumed

            raw_percent = int(getattr(message, "battery_remaining", -1))
            if raw_percent >= 0:
                percent = float(raw_percent)

        full_capacity = self.battery_capacity_mah
        if full_capacity > 0 and consumed_mah >= 0:
            capacity_remaining = max(0, full_capacity - consumed_mah)
            if consumed_mah > 0:
                percent = 100.0 * capacity_remaining / full_capacity
            elif percent > 0:
                capacity_remaining = round(full_capacity * percent / 100.0)
        else:
            capacity_remaining = 0

        percent = round(max(0.0, min(100.0, percent)), 1)
        min_current_for_estimate = 0.5
        if current > min_current_for_estimate and capacity_remaining > 0:
            flight_time_minutes = round(
                (capacity_remaining / (current * 1000.0)) * 60.0,
                1,
            )
        else:
            flight_time_minutes = 0.0

        if voltage_was_reported and voltage < 3.0:
            status = "no_battery"
            voltage = 0.0
            current = 0.0
            percent = 0.0
            consumed_mah = 0
            capacity_remaining = 0
            flight_time_minutes = 0.0
        elif voltage <= 0 and percent <= 0:
            status = "unknown"
        elif current <= min_current_for_estimate:
            status = "idle"
        else:
            status = "flying"

        updated = {
            "voltage": round(max(0.0, voltage), 2),
            "current": round(max(0.0, current), 2),
            "percent": percent,
            "consumedMah": max(0, consumed_mah),
            "capacityRemaining": round(max(0, capacity_remaining), 0),
            "capacityFull": full_capacity,
            "flightTimeMinutes": flight_time_minutes,
            "status": status,
        }
        with self._lock:
            self.telemetry["battery"] = updated

    def _process_heartbeat(self, message: Any) -> None:
        armed = bool(
            message.base_mode
            & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        )
        mode = mavutil.mode_string_v10(message) or "Unknown"
        with self._lock:
            self.telemetry["status"] = {
                "armed": armed,
                "mode": mode,
            }

    def _process_gps(self, message: Any) -> None:
        fix_types = {
            0: "No GPS",
            1: "No Fix",
            2: "2D Fix",
            3: "3D Fix",
            4: "DGPS Fix",
            5: "RTK Float",
            6: "RTK Fixed",
            7: "Static",
            8: "PPP",
        }
        eph = int(getattr(message, "eph", 65535))
        hdop = 0.0 if eph in {0, 65535} else eph / 100.0
        with self._lock:
            self.telemetry["gps"] = {
                "fixType": fix_types.get(int(message.fix_type), "Unknown"),
                "satellites": int(getattr(message, "satellites_visible", 0)),
                "hdop": round(hdop, 2),
            }

    def _process_attitude(self, message: Any) -> None:
        with self._lock:
            self.telemetry["attitude"] = {
                "roll": round(degrees(float(message.roll)), 1),
                "pitch": round(degrees(float(message.pitch)), 1),
                "yaw": round(degrees(float(message.yaw)), 1),
            }

    def _process_vfr(self, message: Any) -> None:
        with self._lock:
            self.telemetry["navigation"]["groundSpeed"] = round(
                float(getattr(message, "groundspeed", 0.0)),
                1,
            )
            self.telemetry["navigation"]["airSpeed"] = round(
                float(getattr(message, "airspeed", 0.0)),
                1,
            )

    def _process_radio(self, message: Any) -> None:
        raw_rssi = int(getattr(message, "rssi", 0) or 0)
        if raw_rssi == 255:
            raw_rssi = 0
        rx_errors = float(getattr(message, "rxerrors", 0) or 0)
        fixed = float(getattr(message, "fixed", 0) or 0)
        total = rx_errors + fixed
        packet_loss = round((rx_errors / total) * 100.0, 1) if total else 0.0
        with self._lock:
            self.telemetry["communication"] = {
                "rssi": raw_rssi,
                "packetLoss": packet_loss,
            }

    def is_connected_to_drone(self) -> bool:
        with self._lock:
            connected = self.is_connected
            last_packet_time = self.last_packet_time

        if connected and last_packet_time:
            if time.time() - last_packet_time > self.stale_after_seconds:
                with self._lock:
                    self.is_connected = False
                return False
        return connected

    def get_heartbeat_state(self) -> str:
        with self._lock:
            last_heartbeat_time = self.last_heartbeat_time
        if not last_heartbeat_time:
            return "Missing"
        if time.time() - last_heartbeat_time > self.heartbeat_stale_after_seconds:
            return "Stale"
        return "Live"

    def get_telemetry(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self.telemetry)

    def get_diagnostics(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            packet_age = now - self.last_packet_time if self.last_packet_time else None
            heartbeat_age = (
                now - self.last_heartbeat_time
                if self.last_heartbeat_time
                else None
            )
            return {
                "connected": self.is_connected_to_drone(),
                "transport": self.transport,
                "connection": self.connection_string,
                "packetAge": round(packet_age, 2) if packet_age is not None else None,
                "heartbeatAge": (
                    round(heartbeat_age, 2)
                    if heartbeat_age is not None
                    else None
                ),
                "heartbeatStatus": self.get_heartbeat_state(),
                "lastMessageType": self.last_message_type,
                "packetCount": self.packet_count,
                "badDataCount": self.bad_data_count,
                "lastError": self.last_error,
            }

    def disconnect(self, quiet: bool = False) -> None:
        self._stop_event.set()
        with self._lock:
            self.running = False
            self.is_connected = False
            mav = self.mav
            self.mav = None

        if mav is not None:
            try:
                mav.close()
            except Exception:
                pass

        thread = self.thread
        if (
            thread
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)

        self.thread = None
        self.transport = "disconnected"
        self._first_packet_event.clear()
        self._heartbeat_event.clear()
        if not quiet:
            print("MAVLink connection pipeline closed")

    @staticmethod
    def _get_connection_type(connection_string: str) -> str:
        value = connection_string.lower()
        if any(token in value for token in ("4g", "5g", "lte", "wwan", "cell")):
            return "CELLULAR"
        if value.startswith(("udp:", "udpin:", "udpout:", "tcp:", "tcpin:")):
            return "WIFI"
        if value.startswith("com") or any(
            token in value for token in ("/dev/tty", "ttyusb", "ttyacm", "usb")
        ):
            return "COM"
        return "WIFI"