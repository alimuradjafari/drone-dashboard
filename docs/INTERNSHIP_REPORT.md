# Internship Project Report

## Drone Fleet Telemetry and Charging Station Dashboard

**Intern:** [Your full name]  
**Organization/Laboratory:** [Laboratory name]  
**Supervisor:** [Supervisor name]  
**Internship period:** Approximately three months — [start date] to [end date]  
**Project repository:** `drone-dashboard`

> Replace the bracketed identity and date fields before submitting this report.

## Executive summary

During my three-month laboratory internship, I worked on the design and implementation of a real-time drone fleet monitoring dashboard. The project receives MAVLink telemetry from Pixhawk-based drones through serial or network connections, converts raw flight-controller messages into a structured application model, and displays the information in a responsive browser dashboard.

The completed prototype covers the main path from hardware to user interface: a Raspberry Pi can read MAVLink frames from a Pixhawk and forward them over UDP; a Python/FastAPI backend can receive multiple serial and UDP streams in parallel; telemetry is classified by MAVLink system ID; and browser clients receive live single-drone and fleet updates over WebSockets. The dashboard visualizes position, navigation, attitude, GPS quality, battery state, communications status, charging-station data, alerts, and fleet-level summaries.

I also added a PostgreSQL-backed signup/sign-in flow, password hashing, JWT generation, environment-based configuration, diagnostic utilities, a charging-station simulator, and automated unit tests. The work required combining web development, asynchronous backend programming, serial/network communication, MAVLink parsing, database integration, authentication, and hardware-oriented debugging.

## 1. Project background

The laboratory needed a practical way to observe drone telemetry without depending on a full ground-control application for every monitoring task. The dashboard needed to present operational data clearly, handle temporary network or hardware loss, and support more than one telemetry source. A related objective was to prepare an interface for a future charging station that could report docking and charging state.

The project therefore acts as an integration layer between flight hardware and users:

```text
Pixhawk / autopilot
        |
        | MAVLink over USB serial
        v
Raspberry Pi forwarder (optional)
        |
        | Raw MAVLink 2 frames over UDP
        v
FastAPI telemetry backend
        |
        | WebSocket + REST/JSON
        v
Browser fleet dashboard
```

Direct Windows COM-port connections are also supported, so the backend can listen to local Pixhawks and UDP-forwarded drones at the same time.

## 2. Internship objectives

The project objectives were to:

1. Build a clear, responsive browser interface for live drone status.
2. Receive and decode MAVLink telemetry from Pixhawk hardware.
3. Support serial and UDP telemetry paths, including Raspberry Pi forwarding.
4. Detect stale or missing telemetry and report disconnection correctly.
5. Support multiple drones and allow operators to switch between them.
6. Present position, flight, battery, GPS, radio, and charging information.
7. Expose telemetry through REST and WebSocket interfaces.
8. Add user signup and sign-in backed by PostgreSQL.
9. Provide diagnostics, simulation tools, tests, and setup documentation.

## 3. Work completed

### 3.1 Dashboard user interface

I developed a two-column responsive dashboard using HTML, CSS, and JavaScript. The interface contains:

- A top status bar with drone ID, connection state, armed state, flight mode, mission state, connection type, and data age.
- A fleet summary showing active drones, armed drones, average battery percentage, and critical-battery count.
- An interactive Leaflet map with a drone marker, home marker, charging-station marker, flight trail, and additional fleet markers.
- Position and navigation values: relative altitude, ground speed, heading, and distance from home.
- Attitude values: roll, pitch, and yaw, with warning styles for large angles.
- GPS quality: fix type, satellite count, and HDOP.
- Battery information: percentage, voltage, current, capacity remaining, estimated flight time, and health.
- Charging information: docking status, charging state, progress, voltage, current, and estimated time to full charge.
- Communications information: RSSI, link state, and packet loss.
- Operator alerts for connection loss and battery warning thresholds.

The layout adapts for desktop, tablet, and mobile widths. A `?demo=1` mode generates simulated telemetry when hardware is unavailable.

### 3.2 Real-time frontend data handling

The browser application uses two WebSocket channels:

- `/ws/telemetry` supplies a backward-compatible primary-drone payload.
- `/ws/fleet` supplies fleet summaries and per-drone telemetry.

The JavaScript data manager maintains application state, deep-merges new telemetry, notifies render listeners, retries disconnected WebSockets, and prevents the single-drone stream from overwriting a selected fleet drone. The fleet manager automatically selects an available drone and updates fleet selector buttons and map markers.

### 3.3 MAVLink receiving and parsing

The backend uses `pymavlink` to open serial, UDP, or TCP connection strings. Each configured target receives its own `DroneConnection` instance and background reader thread. This allows targets such as `COM6`, `COM9`, and `udpin:0.0.0.0:14550` to be monitored concurrently.

The receiver processes these MAVLink messages:

| MAVLink message | Information used |
|---|---|
| `HEARTBEAT` | Armed state, flight mode, heartbeat health |
| `GLOBAL_POSITION_INT` | Latitude, longitude, relative altitude, heading |
| `HOME_POSITION` | Home coordinate for geographic distance calculation |
| `GPS_RAW_INT` | GPS fix type, visible satellites, HDOP |
| `ATTITUDE` | Roll, pitch, yaw converted from radians to degrees |
| `VFR_HUD` | Ground speed and airspeed |
| `NAV_CONTROLLER_OUTPUT` | Distance to the active waypoint |
| `SYS_STATUS` | Battery voltage, current, remaining percentage |
| `BATTERY_STATUS` | Cell voltages, consumed capacity, current, percentage |
| `RADIO_STATUS` / `RADIO` | Signal strength and estimated packet loss |

The receiver distinguishes a live packet stream from heartbeat health. This is useful when routing delivers valid telemetry but omits `HEARTBEAT`. Configuration can require a heartbeat when stricter behavior is needed.

### 3.4 Fleet aggregation

MAVLink messages contain a source system ID (`sysid`). The fleet layer groups messages by this ID and creates stable display identifiers such as `Drone-001`. Each fleet state is protected with re-entrant locks because messages are processed by background threads while FastAPI reads snapshots asynchronously.

Only recently active drones are included. When telemetry becomes older than the configured stale threshold, the drone is removed from the active result. The fleet summary calculates active count, armed count, average battery level, and a list of drones below the critical-battery threshold.

### 3.5 Battery calculations

The backend normalizes MAVLink battery units and handles unavailable sentinel values. It can:

- Sum valid cell voltages from `BATTERY_STATUS`.
- Convert millivolts to volts and centiamps to amps.
- Preserve the previous valid reading when a controller reports an unavailable value.
- Estimate remaining capacity from configured full capacity and consumed mAh.
- Estimate flight time from remaining capacity and current draw.
- Classify battery state as unknown, no battery, idle, or flying.
- Classify display health as healthy, low, or critical.

### 3.6 FastAPI service

The FastAPI application performs database table initialization, starts connection-monitoring tasks, refreshes cached telemetry at 20 Hz, and broadcasts WebSocket data at 10 Hz. It provides REST endpoints for health, current telemetry, diagnostics, fleet state, authentication, and charging updates.

An optional API key protects telemetry and charging endpoints. For WebSockets, the key is supplied as a `token` query parameter. CORS middleware supports the browser dashboard during development.

### 3.7 Raspberry Pi integration

The Raspberry Pi forwarder opens the Pixhawk serial device, waits for a heartbeat, reads complete MAVLink messages, extracts the original raw frame, and forwards each frame to one or more UDP targets. It reconnects automatically after serial or device errors.

This design keeps MAVLink parsing at the laptop backend and lets the Pi act as a lightweight transport bridge. Multiple UDP targets can be used for hotspot and VPN routes.

### 3.8 Charging-station preparation

I implemented a typed charging telemetry model and a `PUT /api/charging` endpoint. Charging updates include docking state, status, progress, voltage, current, and ETA. If updates stop for longer than the configured timeout, the backend reports the station as offline.

A simulator sends one update per second and gradually increases charge progress. It provides a test path before final charging hardware is available.

### 3.9 Authentication and database work

The authentication implementation includes:

- PostgreSQL persistence through asynchronous SQLAlchemy and `asyncpg`.
- A unique indexed email field for user accounts.
- Pydantic email validation.
- Password hashing through Passlib and bcrypt.
- JWT access tokens containing the user ID, email, and expiration time.
- Separate signup and login API routes.
- A browser login/signup form with validation, error reporting, token storage, and dashboard redirect.

During integration I diagnosed several environment-related problems: missing interpreter packages in Pylance, undeclared SQLAlchemy/authentication dependencies, duplicate FastAPI lifespan definitions that prevented table creation, CORS preflight rejection, stale server processes, and an incompatibility between Passlib 1.7.4 and bcrypt 5.0.0. The project now pins bcrypt 4.0.1 for compatibility.

## 4. Engineering approach

### Reliability

- Connection targets retry independently rather than blocking one another.
- Passive UDP listeners remain open while waiting for traffic.
- Reader threads treat malformed individual messages as non-fatal.
- Stale-packet and stale-heartbeat timers provide explicit link health.
- Shared data is copied before being returned to avoid accidental mutation.
- WebSocket clients reconnect automatically after disconnection.
- Charging telemetry becomes offline automatically when updates stop.

### Diagnostics

Two focused diagnostic utilities were added:

- A raw UDP probe verifies whether datagrams physically reach the Windows host.
- A MAVLink probe verifies decoding and counts each received message type.

This separation helps locate faults at the network layer before debugging MAVLink parsing.

### Configuration

Operational settings are loaded from `drone-backend/.env`. Helper functions parse comma-separated lists, booleans, integers, and floats and reject invalid values early. Configurable settings include connection targets, timeouts, stream rate, source IDs, battery capacity, charging freshness, CORS origins, and API key.

## 5. Testing and validation

The repository contains nine unit tests covering battery message processing, unavailable-value behavior, disconnected payload copying, transport naming, geographic distance, API-key enforcement, and charging freshness.

At the time of this report, five tests pass and four require maintenance:

- Two battery tests expect the older three-field battery structure, while the implementation now returns additional derived fields.
- Two tests call helper methods on `FleetManager` that now belong to `DroneConnection` or have been renamed.

This result does not mean the corresponding implementation is necessarily broken; it shows that the tests were not fully updated after the fleet and battery models evolved. Updating these tests is a recommended next action. Authentication hashing and REST reachability were also checked directly during integration.

## 6. Challenges and solutions

### Hardware and network variability

Serial port names can change, UDP traffic can be consumed by another application, cellular networks commonly use CGNAT, and a valid packet stream may not include every expected message. The project addresses these problems with configurable targets, parallel reconnect loops, raw and decoded probes, stale-data detection, and a recommended Tailscale route for cellular operation.

### Concurrent real-time processing

MAVLink receive calls are blocking, while FastAPI and WebSockets are asynchronous. I separated these concerns: blocking hardware reads run in daemon threads, while FastAPI periodically reads locked snapshots and broadcasts them from the event loop.

### Evolving single-drone and fleet designs

The initial single-drone data path was retained for backward compatibility while a fleet-level stream was added. The frontend explicitly gives fleet selection priority and uses the single-drone channel as fallback.

### Authentication integration

Authentication required coordination between the browser, FastAPI, PostgreSQL, SQLAlchemy, Pydantic, Passlib, bcrypt, and JWT libraries. Troubleshooting exposed why dependency pinning, correct virtual-environment selection, startup lifecycle design, and CORS handling are important in a full-stack system.

## 7. Skills developed

This internship developed practical experience in:

- Python backend development with FastAPI and Pydantic.
- Asynchronous tasks, WebSockets, and threaded I/O.
- MAVLink protocol handling with `pymavlink`.
- Serial communication and UDP networking.
- Raspberry Pi integration with Pixhawk hardware.
- JavaScript state management and DOM rendering.
- Responsive UI design and interactive mapping with Leaflet.
- PostgreSQL, asynchronous SQLAlchemy, and data modeling.
- Password hashing, JWT authentication, and API protection.
- Unit testing, diagnostic scripting, log analysis, and dependency debugging.
- Writing interface contracts and deployment/setup instructions.

## 8. Current limitations and recommended next work

1. Move the JWT secret and database URL out of source files and into environment variables.
2. Protect dashboard routes by validating JWTs server-side; the current browser guard only checks that a token exists.
3. Use one configurable backend base URL for login, REST, and both WebSockets. The current working tree uses port 8002 for authentication and port 8000 for telemetry due to a local stale-process conflict encountered during development.
4. Restrict production CORS origins instead of using a wildcard.
5. Add password length/strength rules, token refresh or logout, rate limiting, and account recovery.
6. Add database migrations with Alembic and use a managed secret for database credentials.
7. Persist telemetry logs; the `TelemetryLog` model exists but ingestion does not currently write records.
8. Update the four stale unit tests and add integration tests for API routes and WebSockets.
9. Remove the duplicate, unused `_fleet_manager.py` implementation or make it the single imported source.
10. Replace hard-coded map home/station markers and sample coordinates with backend-provided values.
11. Correct visible character-encoding artifacts in a few source comments and UI degree/icon strings.
12. Add structured logging, deployment service definitions, TLS, and production monitoring.

## 9. Project outcome

The project produced a working laboratory prototype that demonstrates a complete telemetry path from Pixhawk hardware to a multi-drone browser dashboard. It can receive real MAVLink traffic through local serial ports or a Raspberry Pi UDP bridge, identify active drones, calculate operational metrics, distribute updates in real time, and present them through an operator-oriented interface. It also establishes foundations for authenticated access, charging-station integration, diagnostics, and future telemetry persistence.

## 10. Conclusion

This internship gave me experience with the full lifecycle of an engineering prototype: understanding the hardware interface, defining the telemetry contract, implementing data acquisition, building backend and frontend components, diagnosing network and dependency problems, testing behavior, and documenting handoff requirements. The final system is a useful base for continued laboratory development and clearly identifies the remaining steps required for production deployment.

