"""
Handles connection to the drone via MAVLink (Unified Setup)
"""
from pymavlink import mavutil
import threading
import time
from datetime import datetime
import serial  # Required to catch hard OS-level USB extraction errors

class DroneConnection:
    def __init__(self):
        self.mav = None
        self.is_connected = False
        self.telemetry = {
            "position": {"lat": 0, "lng": 0, "alt": 0, "heading": 0},
            "battery": {"voltage": 0, "current": 0, "percent": 0},
            "status": {"armed": False, "mode": "Unknown"},
            "gps": {"fixType": "No Fix", "satellites": 0, "hdop": 0},
            "attitude": {"roll": 0, "pitch": 0, "yaw": 0},
            "navigation": {"groundSpeed": 0, "airSpeed": 0}
        }
        self.last_update = None
        self.running = False
        self.thread = None
        self.last_heartbeat_time = 0  # Tracking time window to handle physical disconnects

    def connect(self, connection_string='COM6'):
        """
        Connect to the drone
        connection_string: 'COM6' for USB (Windows)
                        '/dev/ttyUSB0' for USB (Linux)
                        'udpin:0.0.0.0:14550' for UDP/Hotspot
        """
        try:
            print(f"Connecting to Pixhawk at: {connection_string}")
            self.mav = mavutil.mavlink_connection(connection_string)
            
            # Wait for heartbeat (means drone is connected)
            print(" Waiting for heartbeat from Pixhawk...")
            self.mav.wait_heartbeat()
            print(" Connected to Pixhawk!")
            
            self.is_connected = True
            self.last_heartbeat_time = time.time()  # Initialize heartbeat clock
            
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
        print("Started reading telemetry background thread")

    def _read_telemetry_loop(self):
        """Unified background loop handling both Network/UDP timeouts and Serial/USB exceptions"""
        last_packet_time = time.time()
        self.last_heartbeat_time = time.time()
        
        while self.running and self.is_connected:
            try:
                # Non-blocking check with a 1-second timeout window
                msg = self.mav.recv_match(blocking=True, timeout=1.0)
            
                if msg is None:
                    # Watchdog check for dropped data links (Hotspot fade or quiet serial line)
                    if time.time() - last_packet_time > 4.0:
                        print("Lost telemetry stream (Heartbeat timeout / data stopped)")
                        self.is_connected = False
                        break
                    continue
                
                # Active packet received, bump the clocks to prevent watchdog drops
                last_packet_time = time.time()
                self.last_heartbeat_time = time.time()
                
                msg_type = msg.get_type()
                
                if msg_type == 'GLOBAL_POSITION_INT':
                    self._process_position(msg)
                elif msg_type == 'BATTERY_STATUS':
                    self._process_battery(msg)
                elif msg_type == 'HEARTBEAT':
                    self._process_heartbeat(msg)
                elif msg_type == 'GPS_RAW_INT':
                    self._process_gps(msg)
                elif msg_type == 'ATTITUDE':
                    self._process_attitude(msg)
                elif msg_type == 'VFR_HUD':
                    self._process_vfr(msg)
                    
                self.last_update = datetime.now()
                
            except (serial.SerialException, OSError, AttributeError) as e:
                # Catches immediate OS-level hardware disconnections (USB pulled out)
                print(f" Physical Hardware Connection Broken: {e}")
                self.is_connected = False
                break
            except Exception as e:
                print(f"Error parsing MAVLink packet data: {e}")
                time.sleep(0.1)
    
    def _process_position(self, msg):
        self.telemetry["position"] = {
            "lat": msg.lat / 1e7,
            "lng": msg.lon / 1e7,
            "alt": msg.relative_alt / 1000,
            "heading": msg.hdg / 100
        }
    
    def _process_battery(self, msg):
        voltage = msg.voltages[0] / 1000 if msg.voltages else 0
        current = msg.current_battery / 100 if msg.current_battery else 0
        self.telemetry["battery"] = {
            "voltage": round(voltage, 1),
            "current": round(current, 1),
            "percent": msg.battery_remaining if msg.battery_remaining else 0
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
            "hdop": msg.eph / 100 if msg.eph else 0
        }
    
    def _process_attitude(self, msg):
        self.telemetry["attitude"] = {
            "roll": round(msg.roll * 180 / 3.14159, 1),
            "pitch": round(msg.pitch * 180 / 3.14159, 1),
            "yaw": round(msg.yaw * 180 / 3.14159, 1)
        }
    
    def _process_vfr(self, msg):
        self.telemetry["navigation"] = {
            "groundSpeed": msg.groundspeed,
            "airSpeed": msg.airspeed
        }
    
    def get_telemetry(self):
        return self.telemetry.copy()
    
    def is_connected_to_drone(self):
        """Check if drone is connected (Evaluates watchdog status in real-time)"""
        if self.is_connected and (time.time() - self.last_heartbeat_time > 4.0):
            print(" Heartbeat tracker timed out. Marking connection offline.")
            self.is_connected = False
        return self.is_connected
    
    def disconnect(self):
        self.running = False
        self.is_connected = False
        if self.mav:
            try:
                self.mav.close()
            except:
                pass
        print(" Connection pipeline cleanly closed down.")