

# ============================================================
# FLEET MANAGER  - one DroneConnection per USB/UDP port
# ============================================================

class _DroneState:
    """Lightweight per-drone telemetry bucket, keyed by sysid."""

    def __init__(self, drone_id, sysid, battery_capacity_mah):
        self.drone_id = drone_id
        self.sysid = sysid
        self.battery_capacity_mah = battery_capacity_mah
        self.last_packet_time = 0.0
        self.last_heartbeat_time = 0.0
        self.last_update = None
        self.home_position = None
        self._lock = threading.RLock()
        self.telemetry = self._blank()

    def _blank(self):
        cap = self.battery_capacity_mah
        return {
            "position":      {"lat": 0.0, "lng": 0.0, "alt": 0.0, "heading": 0.0},
            "attitude":      {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            "navigation":    {"groundSpeed": 0.0, "airSpeed": 0.0, "distanceFromHome": 0.0, "distanceToWaypoint": 0.0},
            "gps":           {"fixType": "No GPS", "satellites": 0, "hdop": 0.0},
            "battery":       {"voltage": 0.0, "current": 0.0, "percent": 0.0, "consumedMah": 0,
                              "capacityRemaining": 0, "capacityFull": cap, "flightTimeMinutes": 0.0, "status": "unknown"},
            "status":        {"armed": False, "mode": "Unknown"},
            "communication": {"rssi": 0, "packetLoss": 0.0},
        }

    def touch(self, now):
        with self._lock:
            self.last_packet_time = now
            self.last_update = datetime.now()

    def is_active(self, stale_after):
        return bool(self.last_packet_time and time.time() - self.last_packet_time <= stale_after)

    def snapshot(self):
        with self._lock:
            return deepcopy(self.telemetry)


_FIX_TYPES = {
    0: "No GPS", 1: "No Fix", 2: "2D Fix", 3: "3D Fix",
    4: "DGPS Fix", 5: "RTK Float", 6: "RTK Fixed", 7: "Static", 8: "PPP",
}


class FleetManager:
    """Manages one DroneConnection per connection target, all in parallel.

    Two USB Pixhawks (COM6, COM9) each get their own DroneConnection thread.
    Messages are funnelled into a shared _fleet dict keyed by MAVLink sysid.
    """

    def __init__(
        self,
        *,
        stale_after_seconds=8.0,
        heartbeat_stale_after_seconds=5.0,
        require_heartbeat=False,
        battery_capacity_mah=5000,
        request_stream_rate_hz=20,
        source_system=255,
        source_component=190,
    ):
        self.stale_after_seconds = stale_after_seconds
        self.heartbeat_stale_after_seconds = heartbeat_stale_after_seconds
        self.require_heartbeat = require_heartbeat
        self.battery_capacity_mah = battery_capacity_mah
        self.request_stream_rate_hz = request_stream_rate_hz
        self.source_system = source_system
        self.source_component = source_component

        self._connections: dict[str, DroneConnection] = {}
        self._fleet: dict[int, _DroneState] = {}
        self._lock = threading.RLock()

    # -- connection lifecycle --

    def _make_connection(self):
        return DroneConnection(
            stale_after_seconds=self.stale_after_seconds,
            heartbeat_stale_after_seconds=self.heartbeat_stale_after_seconds,
            require_heartbeat=self.require_heartbeat,
            battery_capacity_mah=self.battery_capacity_mah,
            request_stream_rate_hz=self.request_stream_rate_hz,
            source_system=self.source_system,
            source_component=self.source_component,
        )

    def connect(self, target, initial_timeout):
        """Open *target* if not already open."""
        with self._lock:
            conn = self._connections.get(target)
        if conn is None:
            conn = self._make_connection()
            orig_dispatch = conn._dispatch_message

            def _patched(msg_type, msg, now, _orig=orig_dispatch, _tgt=target):
                _orig(msg_type, msg, now)
                self._fleet_dispatch(_tgt, msg_type, msg, now)

            conn._dispatch_message = _patched
            with self._lock:
                self._connections[target] = conn
        return conn.connect(target, initial_timeout)

    def disconnect(self, quiet=False):
        with self._lock:
            conns = list(self._connections.values())
        for c in conns:
            try:
                c.disconnect(quiet=quiet)
            except Exception:
                pass

    def is_connected_to_drone(self):
        with self._lock:
            return any(c.is_connected_to_drone() for c in self._connections.values())

    def is_endpoint_open(self, target=None):
        with self._lock:
            if target:
                c = self._connections.get(target)
                return c.is_endpoint_open() if c else False
            return any(c.is_endpoint_open() for c in self._connections.values())

    def get_diagnostics(self):
        with self._lock:
            conns = list(self._connections.values())
        if not conns:
            return {
                "connected": False, "transport": "disconnected",
                "heartbeatStatus": "Missing", "heartbeatAge": None,
                "packetAge": None, "packetCount": 0, "badDataCount": 0,
                "lastMessageType": None,
            }
        best = max(conns, key=lambda c: c.last_packet_time or 0.0)
        return best.get_diagnostics()

    # -- fleet dispatch (receives every MAVLink message from every port) --

    def _get_or_create(self, sysid):
        with self._lock:
            if sysid not in self._fleet:
                drone_id = f"Drone-{sysid:03d}"
                self._fleet[sysid] = _DroneState(drone_id, sysid, self.battery_capacity_mah)
                print(f"[Fleet] New drone: {drone_id}  sysid={sysid}")
            return self._fleet[sysid]

    def _fleet_dispatch(self, target, msg_type, msg, now):
        try:
            sysid = int(getattr(msg, "get_srcSystem", lambda: 1)()) or 1
        except Exception:
            sysid = 1

        state = self._get_or_create(sysid)
        state.touch(now)

        try:
            if msg_type == "HEARTBEAT":
                with state._lock:
                    state.last_heartbeat_time = now
                    state.telemetry["status"] = {
                        "armed": bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED),
                        "mode": mavutil.mode_string_v10(msg) or "Unknown",
                    }

            elif msg_type == "GLOBAL_POSITION_INT":
                hdg = getattr(msg, "hdg", 65535)
                pos = {
                    "lat": float(msg.lat) / 1e7,
                    "lng": float(msg.lon) / 1e7,
                    "alt": float(msg.relative_alt) / 1000.0,
                    "heading": round(hdg / 100.0, 2) if hdg != 65535 else 0.0,
                }
                with state._lock:
                    state.telemetry["position"] = pos
                    home = state.home_position
                if home:
                    dist = DroneConnection._distance_metres(
                        pos["lat"], pos["lng"], home[0], home[1]
                    )
                    with state._lock:
                        state.telemetry["navigation"]["distanceFromHome"] = dist

            elif msg_type == "ATTITUDE":
                with state._lock:
                    state.telemetry["attitude"] = {
                        "roll":  round(degrees(float(msg.roll)), 1),
                        "pitch": round(degrees(float(msg.pitch)), 1),
                        "yaw":   round(degrees(float(msg.yaw)), 1),
                    }

            elif msg_type == "VFR_HUD":
                with state._lock:
                    state.telemetry["navigation"]["groundSpeed"] = round(
                        float(getattr(msg, "groundspeed", 0.0)), 1
                    )
                    state.telemetry["navigation"]["airSpeed"] = round(
                        float(getattr(msg, "airspeed", 0.0)), 1
                    )

            elif msg_type == "GPS_RAW_INT":
                eph = int(getattr(msg, "eph", 65535))
                with state._lock:
                    state.telemetry["gps"] = {
                        "fixType": _FIX_TYPES.get(int(msg.fix_type), "Unknown"),
                        "satellites": int(getattr(msg, "satellites_visible", 0)),
                        "hdop": round(0.0 if eph in {0, 65535} else eph / 100.0, 2),
                    }

            elif msg_type == "SYS_STATUS":
                v = int(getattr(msg, "voltage_battery", 0))
                c = int(getattr(msg, "current_battery", -1))
                rem = int(getattr(msg, "battery_remaining", -1))
                with state._lock:
                    state.telemetry["battery"]["voltage"] = round(v / 1000.0, 2)
                    if c >= 0:
                        state.telemetry["battery"]["current"] = round(c / 100.0, 2)
                    if 0 <= rem <= 100:
                        state.telemetry["battery"]["percent"] = float(rem)

            elif msg_type == "HOME_POSITION":
                with state._lock:
                    state.home_position = (
                        float(msg.latitude) / 1e7,
                        float(msg.longitude) / 1e7,
                    )

        except Exception as exc:
            print(f"[Fleet] {msg_type} sysid={sysid}: {exc}")

    # -- fleet query (called by main.py every 50 ms) --

    def get_fleet(self):
        """Return snapshot of all active drones, keyed by droneId."""
        now = time.time()
        result = {}

        with self._lock:
            sysids = list(self._fleet.keys())

        for sysid in sysids:
            with self._lock:
                state = self._fleet.get(sysid)
            if state is None or not state.is_active(self.stale_after_seconds):
                continue
            snap = state.snapshot()
            status = snap.get("status", {})
            age = now - state.last_packet_time if state.last_packet_time else None
            hb_live = bool(
                state.last_heartbeat_time
                and now - state.last_heartbeat_time < self.heartbeat_stale_after_seconds
            )
            result[state.drone_id] = {
                **snap,
                "droneId": state.drone_id,
                "sysid": sysid,
                "connectionStatus": "Connected",
                "armed": bool(status.get("armed")),
                "flightMode": str(status.get("mode", "Unknown")),
                "connectionType": "COM",
                "heartbeatStatus": "Live" if hb_live else "Stale",
                "packetAge": round(age, 2) if age is not None else None,
            }

        if not result:
            result["Drone-001"] = {
                **_DroneState("Drone-001", 1, self.battery_capacity_mah)._blank(),
                "droneId": "Drone-001",
                "sysid": 1,
                "connectionStatus": "Disconnected",
                "armed": False,
                "flightMode": "Unknown",
                "connectionType": "COM",
                "heartbeatStatus": "Missing",
                "packetAge": None,
            }

        return result

    def get_fleet_summary(self, fleet):
        """Aggregate stats across the active fleet."""
        active = len(fleet)
        armed = sum(1 for d in fleet.values() if d.get("armed"))
        batts = [d.get("battery", {}).get("percent", 0) for d in fleet.values()]
        avg = round(sum(batts) / active, 1) if active else 0.0
        crit = [
            d["droneId"]
            for d in fleet.values()
            if d.get("battery", {}).get("percent", 100) < 15
        ]
        return {
            "activeDrones": active,
            "armedDrones": armed,
            "avgBatteryPercent": avg,
            "criticalBattery": crit,
        }
