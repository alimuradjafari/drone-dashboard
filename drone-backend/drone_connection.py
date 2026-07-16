"""
Handles connection to the drone via MAVLink (Unified Setup) - High Refresh Patch
"""
from pymavlink import mavutil
import threading
import time
from datetime import datetime
import serial  # Required to catch hard OS-level USB extraction errors
from math import atan2, cos, radians, sin, sqrt

class DroneConnection:
    def __init__(self):
        self.mav = None
        self.is_connected = False
        self.telemetry = {
            "position": {"lat": 0.0, "lng": 0.0, "alt": 0.0, "heading": 0.0},
            "battery": {"voltage": 0.0, "current": 0.0, "percent": 0},
            "status": {"armed": False, "mode": "Unknown"},
            "gps": {"fixType": "No Fix", "satellites": 0, "hdop": 0.0},
            "attitude": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            "navigation": {"groundSpeed": 0.0, "airSpeed": 0.0, "distanceFromHome": 0.0},
            "communication": {"rssi": 0, "packetLoss": 0.0}
        }
        self.last_update = None
        self.running = False
        self.thread = None
        self.last_heartbeat_time = 0  # Tracking time window to handle physical disconnects
        self.connection_string = None
        self.transport = "disconnected"
        self.home_position = None

    def connect(self, connection_string='COM9', heartbeat_timeout=5.0):
        """Connect to the drone."""
        try:
            print(f"Connecting to Pixhawk at: {connection_string}")
            self.disconnect(quiet=True)
            self.mav = mavutil.mavlink_connection(connection_string)
            
            print(" Waiting for heartbeat from Pixhawk...")
            heartbeat = self.mav.wait_heartbeat(timeout=heartbeat_timeout)
            if heartbeat is None:
                raise TimeoutError(f"No MAVLink heartbeat within {heartbeat_timeout:g} seconds")
            print(" Connected to Pixhawk!")
            
            self.is_connected = True
            self.connection_string = connection_string
            self.transport = self._transport_name(connection_string)
            self.last_heartbeat_time = time.time()  # Initialize heartbeat clock
            
            # Force the Pixhawk stream rate limits immediately over serial interface links
            self.mav.mav.request_data_stream_send(
                self.mav.target_system, self.mav.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, 20, 1
            )
            
            # Start reading telemetry in background
            self.start_reading()
            return True
            
        except Exception as e:
            print(f" Connection failed: {e}")
            self.is_connected = False
            return False
    
    def start_reading(self):
        """Start background thread to read telemetry"""
        if self.thread and self.thread.is_alive():
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._read_telemetry_loop)
        self.thread.daemon = True
        self.thread.start()
        print(" Started reading telemetry background thread")

    def _read_telemetry_loop(self):
        """Unified background loop with optimized serial buffer drain handling"""
        last_packet_time = time.time()
        self.last_heartbeat_time = time.time()
        
        while self.running and self.is_connected:
            try:
                has_packets = False
                
                while True:
                    msg = self.mav.recv_match(blocking=False)
                    if msg is None:
                        break  # Buffer cleared out completely!
                    
                    has_packets = True
                    last_packet_time = time.time()
                    
                    msg_type = msg.get_type()
                    
                    if msg_type == 'GLOBAL_POSITION_INT':
                        self._process_position(msg)
                    elif msg_type in ['SYS_STATUS', 'BATTERY_STATUS']:
                        self._process_battery(msg)
                    elif msg_type == 'NAV_CONTROLLER_OUTPUT':
                        self._process_nav_output(msg)
                    elif msg_type == 'HEARTBEAT':
                        self.last_heartbeat_time = time.time()  # True physical heartbeat pulse
                        self._process_heartbeat(msg)
                    elif msg_type == 'GPS_RAW_INT':
                        self._process_gps(msg)
                    elif msg_type == 'ATTITUDE':
                        self._process_attitude(msg)
                    elif msg_type == 'VFR_HUD':
                        self._process_vfr(msg)
                    elif msg_type in ['RADIO_STATUS', 'RADIO']:
                        self._process_radio(msg)
                    elif msg_type == 'HOME_POSITION':
                        self._process_home_position(msg)
                
                # Link Watchdog check if no packets have been found recently
                if not has_packets and (time.time() - last_packet_time > 4.0):
                    print(" Lost telemetry stream (Watchdog timeout / data stopped)")
                    self.is_connected = False
                    break
                
                self.last_update = datetime.now()
                time.sleep(0.01)
                
            except (serial.SerialException, OSError, AttributeError) as e:
                print(f" Physical Hardware Connection Broken: {e}")
                self.is_connected = False
                break
            except Exception as e:
                print(f" Error parsing MAVLink packet data: {e}")
                time.sleep(0.05)
    
    def _process_position(self, msg):
        self.telemetry["position"] = {
            "lat": msg.lat / 1e7,
            "lng": msg.lon / 1e7,
            "alt": msg.relative_alt / 1000.0,
            "heading": msg.hdg / 100.0
        }
        if self.home_position:
            self.telemetry["navigation"]["distanceFromHome"] = self._distance_metres(
                self.telemetry["position"]["lat"], self.telemetry["position"]["lng"],
                self.home_position[0], self.home_position[1]
            )

    def _process_home_position(self, msg):
        self.home_position = (msg.latitude / 1e7, msg.longitude / 1e7)

    @staticmethod
    def _distance_metres(lat1, lon1, lat2, lon2):
        radius = 6371000.0
        phi1, phi2 = radians(lat1), radians(lat2)
        d_phi, d_lambda = radians(lat2 - lat1), radians(lon2 - lon1)
        a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
        return round(radius * 2 * atan2(sqrt(a), sqrt(1 - a)), 1)

    def _process_nav_output(self, msg):
        # wp_dist represents the distance to the current waypoint or home position in meters
        self.telemetry["navigation"]["distanceFromHome"] = float(getattr(msg, 'wp_dist', 0.0))
    
    def _process_battery(self, msg):
        print(f" Processing battery message: {msg}")
        msg_type = msg.get_type()
        previous = self.telemetry["battery"]
        voltage = previous["voltage"]
        current = previous["current"]
        percent = previous["percent"]

        # Never replace a valid reading with MAVLink's unavailable sentinels.
        if msg_type == 'SYS_STATUS':
            raw_voltage = getattr(msg, 'voltage_battery', 65535)
            if 0 < raw_voltage < 65535:
                voltage = raw_voltage / 1000.0
        elif msg_type == 'BATTERY_STATUS':
            # BATTERY_STATUS contains per-cell voltages. Unused cells are 65535.
            cells = [value for value in getattr(msg, 'voltages', []) if 0 < value < 65535]
            cells.extend(value for value in getattr(msg, 'voltages_ext', []) if 0 < value < 65535)
            if cells:
                voltage = sum(cells) / 1000.0
        else:
            return

        raw_current = getattr(msg, 'current_battery', -1)
        if raw_current >= 0:
            current = raw_current / 100.0

        # Prefer the flight controller's calibrated estimate. Voltage-only pack
        # estimation is chemistry/cell-count dependent and causes unstable values.
        raw_percent = getattr(msg, 'battery_remaining', -1)
        if raw_percent >= 0:
            percent = raw_percent

        self.telemetry["battery"] = {
            "voltage": round(max(0.0, voltage), 2),
            "current": round(max(0.0, current), 1),
            "percent": int(max(0, min(100, percent)))
        }
    
    def _process_heartbeat(self, msg):
        is_armed = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
        mode = mavutil.mode_string_v10(msg)
        self.telemetry["status"] = {
            "armed": is_armed,
            "mode": mode
        }
    
    def _process_gps(self, msg):
        fix_types = {0: "No Fix", 1: "2D Fix", 2: "3D Fix", 3: "DGPS Fix", 4: "RTK Float", 5: "RTK Fixed"}
        self.telemetry["gps"] = {
            "fixType": fix_types.get(msg.fix_type, "Unknown"),
            "satellites": msg.satellites_visible,
            "hdop": msg.eph / 100.0 if msg.eph else 0.0
        }
    
    def _process_attitude(self, msg):
        self.telemetry["attitude"] = {
            "roll": round(msg.roll * 180.0 / 3.141592653589793, 1),
            "pitch": round(msg.pitch * 180.0 / 3.141592653589793, 1),
            "yaw": round(msg.yaw * 180.0 / 3.141592653589793, 1)
        }
    
    def _process_vfr(self, msg):
        #  FIXED: Target the inner properties explicitly so you don't blow away "distanceFromHome"
        self.telemetry["navigation"]["groundSpeed"] = round(msg.groundspeed, 1)
        #self.telemetry["navigation"]["airSpeed"] = round(msg.airspeed, 1)

    def _process_radio(self, msg):
        raw_rssi = float(getattr(msg, 'rssi', 0))
        rssi_dbm = round((raw_rssi / 1.9) - 127) if raw_rssi else 0
        errors = float(getattr(msg, 'rxerrors', 0))
        fixed = float(getattr(msg, 'fixed', 0))
        total = errors + fixed
        packet_loss = round((errors / total) * 100, 1) if total else 0.0
        self.telemetry["communication"] = {"rssi": rssi_dbm, "packetLoss": packet_loss}

    @staticmethod
    def _transport_name(connection_string):
        value = connection_string.lower()
        if value.startswith(('udp:', 'udpin:', 'udpout:')):
            return "Wi-Fi/Cellular UDP"
        if value.startswith(('tcp:', 'tcpin:')):
            return "Network TCP"
        return "USB Serial"
    
    def get_telemetry(self):
        return self.telemetry.copy()
    
    def is_connected_to_drone(self):
        """Check if drone is connected (Evaluates watchdog status in real-time)"""
        if self.is_connected and (time.time() - self.last_heartbeat_time > 4.0):
            print(" Heartbeat tracker timed out. Marking connection offline.")
            self.is_connected = False
        return self.is_connected
    
    def disconnect(self, quiet=False):
        self.running = False
        self.is_connected = False
        if self.mav:
            try:
                self.mav.close()
            except:
                pass
        self.mav = None
        self.transport = "disconnected"
        if not quiet:
            print("Connection pipeline closed.")
