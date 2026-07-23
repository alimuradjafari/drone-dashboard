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
            "battery": {
                "voltage": 0.0, 
                "current": 0.0, 
                "percent": 0, 
                "consumedMah": 0,
                "capacityRemaining": 0,
                "capacityFull": 5000,  # Default to 5000mAh
                "flightTimeMinutes": 0,
                "status": "idle"
            },
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
        self.transport = "disconnected"  # Will be set to COM, WIFI, or CELLULAR
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
            self.transport = self._get_connection_type(connection_string)  
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
        """
        Process battery messages from MAVLink.
        Handles both SYS_STATUS and BATTERY_STATUS messages.
        Uses current integration for accurate battery tracking.
        """
        # Debug print to see raw data
        print({
            "type": msg.get_type(),
            "battery_remaining": getattr(msg, "battery_remaining", None),
            "current_consumed": getattr(msg, "current_consumed", None),
            "voltage_battery": getattr(msg, "voltage_battery", None),
            "voltages": getattr(msg, "voltages", None),
            "current_battery": getattr(msg, "current_battery", None),
        })
        
        msg_type = msg.get_type()
        previous = self.telemetry.get("battery", {})
        
        # Initialize with previous values or defaults
        voltage = previous.get("voltage", 0.0)
        current = previous.get("current", 0.0)
        percent = previous.get("percent", 0)
        consumed_mah = previous.get("consumedMah", 0)
        
        # Process SYS_STATUS message
        if msg_type == 'SYS_STATUS':
            raw_voltage = getattr(msg, 'voltage_battery', 65535)
            if raw_voltage is not None and 0 < raw_voltage < 65535:
                voltage = raw_voltage / 1000.0
                print(f"✅ Voltage from SYS_STATUS: {voltage}V")
            
            raw_percent = getattr(msg, 'battery_remaining', -1)
            if raw_percent >= 0:
                percent = raw_percent
        
        # Process BATTERY_STATUS message
        elif msg_type == 'BATTERY_STATUS':
            # Get voltage from cell voltages
            voltages = getattr(msg, 'voltages', [])
            if voltages and len(voltages) > 0:
                valid_cells = [v for v in voltages if v is not None and 0 < v < 65535]
                if valid_cells:
                    # Check if values are in Volts or millivolts
                    if all(v < 30 for v in valid_cells):
                        voltage = sum(valid_cells)  # Already in Volts
                    else:
                        voltage = sum(valid_cells) / 1000.0  # Convert mV to V
                    print(f"✅ Voltage from BATTERY_STATUS cells: {voltage}V")
            
            # Get current draw
            raw_current = getattr(msg, 'current_battery', -1)
            if raw_current >= 0:
                current = raw_current / 100.0
                print(f"✅ Current: {current}A")
            
            # Get consumed capacity
            raw_consumed = getattr(msg, "current_consumed", -1)
            if raw_consumed >= 0:
                consumed_mah = raw_consumed
                print(f"✅ Consumed capacity updated: {consumed_mah} mAh")
            
            # Get battery percentage (fallback)
            raw_percent = getattr(msg, 'battery_remaining', -1)
            if raw_percent >= 0:
                percent = raw_percent
        
        # ✅ DETECT BATTERY PRESENCE
        # If voltage is below threshold, battery is not connected
        BATTERY_CONNECTED_THRESHOLD = 3.0  # Volts
        if voltage < BATTERY_CONNECTED_THRESHOLD:
            print(f"⚠️ Battery voltage too low ({voltage}V < {BATTERY_CONNECTED_THRESHOLD}V) - assuming battery is disconnected")
            self.telemetry["battery"] = {
                "voltage": 0.0,
                "current": 0.0,
                "percent": 0,
                "consumedMah": 0,
                "capacityRemaining": 0,
                "capacityFull": 5000,
                "flightTimeMinutes": 0,
                "status": "no_battery"
            }
            return  # Stop processing - no battery connected
        
        # ✅ Calculate REAL values using current integration
        from config import BATTERY_CAPACITY_MAH
        FULL_CAPACITY_MAH = BATTERY_CAPACITY_MAH if BATTERY_CAPACITY_MAH > 0 else 5000
        
        # Calculate REAL capacity remaining from consumed capacity
        if consumed_mah > 0:
            capacity_remaining = max(0, FULL_CAPACITY_MAH - consumed_mah)
            real_percent = max(0, min(100, (1 - (consumed_mah / FULL_CAPACITY_MAH)) * 100))
            real_percent = round(real_percent, 1)
        else:
            capacity_remaining = round(FULL_CAPACITY_MAH * percent / 100) if percent > 0 else 0
            real_percent = percent
        
        # Calculate flight time only when actually flying
        MIN_CURRENT_FOR_CALCULATION = 0.5
        flight_time_minutes = 0
        
        if current > MIN_CURRENT_FOR_CALCULATION and capacity_remaining > 0:
            current_ma = current * 1000
            flight_time_minutes = (capacity_remaining / current_ma) * 60
            flight_time_minutes = round(flight_time_minutes, 1)
        
        # Determine battery status
        if current <= MIN_CURRENT_FOR_CALCULATION:
            status = "idle"
        elif flight_time_minutes > 0:
            status = "flying"
        else:
            status = "estimating"
        
        # Update telemetry with REAL values
        self.telemetry["battery"] = {
            "voltage": round(max(0.0, voltage), 2),
            "current": round(max(0.0, current), 1),
            "percent": int(max(0, min(100, real_percent))),
            "consumedMah": consumed_mah if consumed_mah > 0 else 0,
            "capacityRemaining": round(capacity_remaining, 0) if capacity_remaining > 0 else 0,
            "capacityFull": FULL_CAPACITY_MAH,
            "flightTimeMinutes": flight_time_minutes,
            "status": status
        }
    
        print(f"📊 Final battery: {self.telemetry['battery']}")
    
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

    def _get_connection_type(self, connection_string):
        """
        Determine the connection type and return standardized values:
        - COM for USB/Serial connections
        - WIFI for Wi-Fi connections
        - CELLULAR for cellular modem connections
        """
        value = connection_string.lower()
        
        # Check for cellular modems (common cellular connection strings)
        if any(keyword in value for keyword in ['/dev/ttyusb', 'com', 'usb']):
            # Check if it's a cellular modem (usually has specific VID/PID or modem name)
            # You can expand this detection based on your specific hardware
            if any(keyword in value for keyword in ['modem', 'cell', '4g', '5g', 'lte', 'wwan']):
                return "CELLULAR"
            return "COM"
        
        # Check for Wi-Fi/UDP connections
        elif any(keyword in value for keyword in ['udp:', 'udpin:', 'udpout:', 'tcp:', 'tcpin:', 'wifi', 'wireless']):
            # Check if it's a cellular hotspot (often uses specific ports or patterns)
            if any(keyword in value for keyword in ['cell', '4g', '5g', 'lte', 'hotspot']):
                return "CELLULAR"
            return "WIFI"
        
        # Default fallback - check if it looks like a network connection
        elif any(keyword in value for keyword in [':', '/', '192.', '10.', '172.', 'localhost', '127.']):
            return "WIFI"
        
        # If it's a serial port, treat as COM
        elif any(keyword in value for keyword in ['com', 'tty', 'usb']):
            return "COM"
        
        # If we can't determine, default to the raw connection type
        return "WIFI"  # Default to WIFI for unknown network connections
    
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