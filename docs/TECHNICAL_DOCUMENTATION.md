# Drone Dashboard Technical Documentation

## 1. Purpose and scope

This document describes the complete `drone-dashboard` repository as it exists on August 17, 2026. It covers every maintained source, stylesheet, markup, dependency, test, and Markdown file outside the virtual environment. Generated logs, `__pycache__`, the virtual environment, local `.env` secrets, and Git internals are intentionally excluded.

The system monitors one or more MAVLink drones, exposes normalized telemetry through FastAPI, and renders it in a browser. It also includes authentication, charging-station telemetry, Raspberry Pi forwarding, diagnostics, simulation, and tests.

## 2. Repository map

```text
drone-dashboard/
|-- index.html                       Main telemetry dashboard markup
|-- styles.css                       Dashboard theme and responsive layout
|-- script.js                        WebSocket state, rendering, fleet UI, map
|-- login.html                       Signup/sign-in page and client logic
|-- login.css                        Authentication-page styling
|-- image.png                        Raster visual asset retained in repository
|-- DRONE_TEAM_INTERFACE.md          MAVLink handoff contract
|-- NETWORK_SETUP.md                 Pi, UDP, hotspot, VPN, backend instructions
|-- drone-backend/
|   |-- main.py                      FastAPI application and telemetry API
|   |-- drone_connection.py          MAVLink receiver and active FleetManager
|   |-- _fleet_manager.py            Older/duplicate fleet implementation
|   |-- config.py                    Environment parsing and validation
|   |-- database.py                  Async SQLAlchemy engine/session
|   |-- models.py                    User and TelemetryLog ORM models
|   |-- auth.py                      Password hashing and JWT helpers
|   |-- auth_routes.py               Signup and login endpoints
|   |-- mavlink_probe.py             Decoded MAVLink diagnostic listener
|   |-- udp_probe.py                 Raw UDP diagnostic listener
|   |-- test_main.py                 API/support behavior unit tests
|   |-- test_battery.py              Battery processing unit tests
|   `-- requirements.txt             Backend Python dependencies
`-- raspberry-pi/
    |-- mavlink_forwarder.py          Pixhawk serial-to-UDP bridge
    |-- charging_station_simulator.py Charging API simulator
    `-- requirements.txt              Raspberry Pi dependencies
```

## 3. Runtime architecture

### 3.1 Telemetry flow

```text
Pixhawk(s)
   | USB serial directly to Windows (COMx)
   | or USB serial to Raspberry Pi
   |                         |
   |                         `-- raw MAVLink frames --> UDP/14550
   v
FleetManager
   |-- one DroneConnection per configured target
   |-- one receiver thread per connection
   |-- messages grouped by MAVLink sysid
   v
FastAPI cached state (refreshed every 50 ms)
   |-- REST snapshots
   |-- /ws/telemetry at 10 Hz
   `-- /ws/fleet at 10 Hz
   v
Browser state managers --> dashboard renderers --> operator UI
```

### 3.2 Authentication flow

```text
login.html
   | POST signup/login JSON
   v
FastAPI auth router
   |-- Pydantic EmailStr validation
   |-- async SQLAlchemy query
   |-- Passlib/bcrypt hash or verify
   `-- python-jose JWT creation
   v
PostgreSQL users table + token returned to localStorage
```

The current browser guard checks whether `authToken` exists in `localStorage`. The telemetry API does not yet validate that JWT. `DASHBOARD_API_KEY` is a separate optional mechanism for telemetry and charging endpoints.

## 4. Setup

### 4.1 Prerequisites

- Python 3.13 or another version supported by all pinned packages.
- PostgreSQL with a database named `drone_dashboard`.
- A modern browser.
- Optional Pixhawk and serial cable.
- Optional Raspberry Pi for UDP forwarding.

### 4.2 Backend environment

From PowerShell:

```powershell
cd D:\drone-dashboard\drone-backend
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Select `D:\drone-dashboard\drone-backend\venv\Scripts\python.exe` as the VS Code Python interpreter so Pylance resolves project imports.

Create `drone-backend/.env` with settings appropriate for the laboratory. Do not commit secrets.

Example:

```env
MAVLINK_CONNECTIONS=udpin:0.0.0.0:14550,COM9
MAVLINK_INITIAL_PACKET_TIMEOUT=5
MAVLINK_REQUIRE_HEARTBEAT=false
TELEMETRY_STALE_AFTER=8
MAVLINK_HEARTBEAT_STALE_AFTER=5
MAVLINK_REQUEST_STREAM_RATE_HZ=20
MAVLINK_RECONNECT_DELAY=3
MAVLINK_SOURCE_SYSTEM=255
MAVLINK_SOURCE_COMPONENT=190
BATTERY_CAPACITY_MAH=5000
CHARGING_STALE_AFTER=5
CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500
DASHBOARD_API_KEY=
```

Start the canonical backend:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

The current `login.html` points authentication requests to port 8002 because a stale local process blocked port 8000 during troubleshooting. For a clean deployment, configure all browser connections to use one backend port. Until that refactor, a second instance can be started with:

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8002
```

### 4.3 Frontend

Serve the repository root with a local static server rather than relying on direct file access. For example:

```powershell
cd D:\drone-dashboard
python -m http.server 5500
```

Open `http://localhost:5500/login.html`. After successful authentication, the page stores `authToken` and `userName` and redirects to `index.html`.

Add `?demo=1` to the dashboard URL to enable simulated telemetry when a WebSocket is unavailable.

### 4.4 PostgreSQL

The current database URL is defined directly in `database.py`. Create the matching database and credentials before startup. The FastAPI lifespan calls `create_tables()`, which creates missing tables from SQLAlchemy metadata.

For production, replace the hard-coded URL with a `DATABASE_URL` environment variable and use Alembic migrations.

## 5. Configuration reference

| Variable | Default | Meaning |
|---|---:|---|
| `DRONE_ID` | `Drone-001` | Fallback single-drone identifier |
| `MAVLINK_CONNECTIONS` | `udpin:0.0.0.0:14550` | Comma-separated pymavlink targets |
| `MAVLINK_INITIAL_PACKET_TIMEOUT` | legacy timeout or `10` | Initial wait for usable traffic |
| `MAVLINK_HEARTBEAT_TIMEOUT` | `10` | Backward-compatible timeout name |
| `MAVLINK_REQUIRE_HEARTBEAT` | `false` | Require heartbeat instead of any valid packet |
| `TELEMETRY_STALE_AFTER` | `8` | Seconds before packet stream is stale |
| `MAVLINK_HEARTBEAT_STALE_AFTER` | `5` | Seconds before heartbeat is stale |
| `MAVLINK_REQUEST_STREAM_RATE_HZ` | `20` | Requested autopilot stream rate |
| `MAVLINK_RECONNECT_DELAY` | `3` | Delay between target reconnect attempts |
| `MAVLINK_SOURCE_SYSTEM` | `255` | Ground-side MAVLink system ID |
| `MAVLINK_SOURCE_COMPONENT` | `190` | Ground-side MAVLink component ID |
| `CORS_ORIGINS` | local port 5500 origins | Parsed configured origins; current app middleware uses wildcard |
| `DASHBOARD_API_KEY` | empty | Optional telemetry/charging API key |
| `CHARGING_STALE_AFTER` | `5` | Charging update freshness timeout |
| `BATTERY_CAPACITY_MAH` | `5000` | Full capacity used for estimates |

## 6. API reference

### `GET /`

Unauthenticated health response:

```json
{"status": "online", "message": "Drone Telemetry API is running"}
```

### `POST /api/auth/signup`

Request:

```json
{"name": "Operator", "email": "operator@example.com", "password": "secret"}
```

Creates a unique user, hashes the password, and returns a JWT and display name. Returns HTTP 400 when the email already exists and HTTP 422 for validation errors.

### `POST /api/auth/login`

Request:

```json
{"email": "operator@example.com", "password": "secret"}
```

Returns a JWT and display name. Invalid credentials return HTTP 401.

### `GET /api/telemetry/latest`

Returns the cached primary-drone telemetry object. If `DASHBOARD_API_KEY` is configured, send `X-API-Key`.

### `GET /api/status`

Returns transport diagnostics, packet/heartbeat age, counters, last error, configured targets, and stale thresholds.

### `GET /api/fleet`

Returns:

```json
{
  "summary": {
    "activeDrones": 1,
    "armedDrones": 0,
    "avgBatteryPercent": 72.0,
    "criticalBattery": []
  },
  "drones": {
    "Drone-001": {"droneId": "Drone-001", "connectionStatus": "Connected"}
  }
}
```

### `PUT /api/charging`

Request:

```json
{
  "docked": true,
  "status": "Charging",
  "progress": 42,
  "voltage": 25.2,
  "current": 4.2,
  "eta": 29
}
```

Progress is validated between 0 and 100; voltage, current, and ETA must be non-negative.

### `WS /ws/telemetry`

Sends the primary telemetry JSON every 100 ms. If an API key is configured, connect with `?token=KEY`.

### `WS /ws/fleet`

Sends fleet summary and all active drone snapshots every 100 ms. It uses the same optional token query parameter.

## 7. Telemetry data model

The normalized payload contains:

| Group | Important fields |
|---|---|
| Identity/status | `droneId`, `connectionStatus`, `armed`, `flightMode`, `missionStatus`, `connectionType` |
| Position | `lat`, `lng`, `alt`, `heading` |
| Navigation | `groundSpeed`, `airSpeed`, `distanceFromHome`, `distanceToWaypoint` |
| Attitude | `roll`, `pitch`, `yaw` |
| GPS | `fixType`, `satellites`, `hdop` |
| Battery | `percent`, `voltage`, `current`, `consumedMah`, `capacityRemaining`, `capacityFull`, `flightTimeMinutes`, `status` |
| Charging | `docked`, `status`, `progress`, `voltage`, `current`, `eta`, freshness fields |
| Communication | `rssi`, `packetLoss`, link/heartbeat state, packet age, last update |

## 8. File-by-file documentation

### Root frontend files

#### `index.html`

Defines the semantic dashboard layout and all DOM targets used by `script.js`. It loads Leaflet CSS/JS, Tabler icons, Google Inter, `styles.css`, and `script.js`. It does not contain application logic. Its cards cover position/navigation, attitude, GPS, battery, charging, communications, alerts, top-level drone state, fleet selection, and fleet summary.

#### `styles.css`

Defines the dashboard design system using CSS custom properties, base reset, two-column grid, cards, badges, metrics, map styling, battery and charging progress bars, attitude warnings, communication bars, alerts, fleet pills, and fleet summary styles. Breakpoints at 1024, 768, and 480 pixels adapt the display. A reduced-motion query disables animations for users who request it.

#### `script.js`

Contains four main classes:

- `TelemetryManager`: owns default/current telemetry, optional simulation, `/ws/telemetry`, reconnection, deep merge, subscriber notification, battery/link alerts, and command sending.
- `DashboardRenderer`: caches DOM nodes; initializes Leaflet; renders top bar, map/trail, navigation, attitude, GPS, battery, charging, communications, and alerts.
- `FleetManager`: receives `/ws/fleet`, stores fleet data, selects a drone, and maps its schema into the existing single-dashboard model.
- `FleetSwitcherRenderer`: renders fleet statistics, selectable drone pills, and non-selected drone map markers.

On `DOMContentLoaded`, these objects are constructed and both WebSockets start after two seconds. Instances are exposed under `window.__telemetry`, `window.__dashboard`, `window.__fleet`, and `window.__fleetUI` for debugging.

#### `login.html`

Defines the combined signup/sign-in form and includes its JavaScript inline. It switches form mode by changing which button submits and whether name is required. `postAuth()` sends JSON, displays API/network errors, stores the JWT and user name, and redirects after success. The page also renders an animated canvas network of 80 moving nodes.

The API host is derived from `window.location.hostname`; direct file access falls back to `localhost`. The current auth port is 8002.

#### `login.css`

Styles the full-screen authentication view: dark background, canvas layer, glass-like login panel, input/icon alignment, animated heading accent, buttons, disabled-mode appearance, and inline error message. The file currently contains overlapping `.error-msg` styling with `login.html`; this can be consolidated later.

#### `image.png`

A repository raster image asset. It is not referenced by the current HTML or CSS and can be archived or removed after confirming it has no design/reference purpose.

### Backend application files

#### `drone-backend/main.py`

Application composition and runtime entry point. Responsibilities:

- Imports configuration, database models, authentication routes, and the fleet receiver.
- Creates the global active `FleetManager`.
- Defines the disconnected telemetry template and charging model/state.
- Converts fleet snapshots into the backward-compatible primary payload.
- Enriches telemetry with charging and display-oriented battery health.
- Runs one asynchronous reconnect loop per connection target.
- Refreshes cached primary/fleet payloads every 50 ms.
- Creates database tables during FastAPI lifespan startup.
- Starts and cancels connection/update tasks cleanly.
- Configures CORS and registers REST/WebSocket routes.
- Broadcasts telemetry and fleet data every 100 ms.

Important concurrency boundary: blocking connection calls run through `asyncio.to_thread`, MAVLink reads run in `DroneConnection` threads, and WebSocket broadcasts stay in the FastAPI event loop.

#### `drone-backend/drone_connection.py`

The core hardware/data-processing module.

`DroneConnection` manages one pymavlink endpoint. It resets state on connection, starts a daemon reader thread, waits for first packet or heartbeat, requests all streams once, decodes supported messages, tracks diagnostics, detects stale traffic, and closes resources.

`_process_battery()` is the most calculation-heavy routine. It supports both main battery message formats, sentinel handling, unit conversion, derived capacity, flight-time estimation, and status classification.

The same file also defines the active `_DroneState` and `FleetManager`. `FleetManager` creates one `DroneConnection` per target, wraps its dispatch method so each message also updates a sysid-specific fleet state, returns active snapshots, and computes fleet summaries.

#### `drone-backend/_fleet_manager.py`

Contains an older copy of `_DroneState` and `FleetManager`. It is not imported by `main.py` and, as a standalone file, lacks imports for names such as `threading`, `datetime`, `deepcopy`, `time`, `degrees`, `mavutil`, and `DroneConnection`. Treat it as historical/incomplete code. Recommended action: remove it or refactor the active fleet implementation into this module with complete imports and tests.

#### `drone-backend/config.py`

Loads `.env` from the backend directory independently of the current working directory. Provides strict parsers for comma-separated strings, booleans, floats, and integers. Exposes validated constants and rejects invalid timeouts/source IDs during import.

#### `drone-backend/database.py`

Creates the asynchronous PostgreSQL SQLAlchemy engine, `AsyncSession` factory, declarative base class, request-scoped `get_db()` dependency, and `create_tables()` helper. The database URL is currently hard-coded and must be moved to configuration before sharing or deployment.

#### `drone-backend/models.py`

Defines two ORM tables:

- `User`: integer ID, name, unique indexed email, hashed password, creation time.
- `TelemetryLog`: drone/system IDs, position, heading, speed, battery, mode, armed state, and logged time.

`TelemetryLog` is schema preparation; no active code currently inserts telemetry records.

#### `drone-backend/auth.py`

Creates the Passlib bcrypt context and provides `hash_password()`, `verify_password()`, `create_token()`, and `decode_token()`. JWTs use HS256 and expire after eight hours. The signing secret is hard-coded and must be replaced with a strong environment value before deployment.

#### `drone-backend/auth_routes.py`

Defines Pydantic request bodies and `/api/auth` routes. Signup checks for an existing email, hashes the password, commits the user, refreshes the generated ID, and returns a token. Login retrieves the user and verifies the password before returning a token.

Neither route currently normalizes email case, enforces password policy, catches database availability errors, or rate-limits attempts.

#### `drone-backend/mavlink_probe.py`

A command-line decoded-packet probe. It opens any pymavlink connection string, prints valid message type/source/count, counts `BAD_DATA`, and emits summaries at a configurable interval. Other listeners must release the UDP port first.

#### `drone-backend/udp_probe.py`

A lower-level diagnostic that binds a UDP socket and prints datagram size, sender, and the first 16 bytes in hex. It proves network delivery without depending on MAVLink validity.

#### `drone-backend/test_main.py`

Uses `unittest` and mocks to test disconnected-template isolation, transport naming, geographic distance, API-key rejection, and charging freshness. Two tests currently target methods that are no longer exposed on the global `FleetManager` and need updating.

#### `drone-backend/test_battery.py`

Creates lightweight fake MAVLink messages and tests `SYS_STATUS`, `BATTERY_STATUS`, and unavailable values. Two assertions still expect the old three-key battery object and must be updated to assert relevant fields within the richer model.

#### `drone-backend/requirements.txt`

Pins the backend stack: FastAPI, Uvicorn, WebSockets, pymavlink, dotenv, pyserial, SQLAlchemy, asyncpg, Passlib, bcrypt, python-jose, and email-validator. Some dependency lines are duplicated; remove duplicates during the next dependency cleanup. `bcrypt==4.0.1` is intentional because Passlib 1.7.4 is incompatible with bcrypt 5.0.0 in this application.

### Raspberry Pi files

#### `raspberry-pi/mavlink_forwarder.py`

Reads environment variables for Pixhawk device, baud rate, and one or more `host:port` UDP targets. It opens the serial MAVLink connection, waits up to ten seconds for heartbeat, forwards each raw message buffer to all targets, and reconnects after an `OSError` or timeout.

#### `raspberry-pi/charging_station_simulator.py`

CLI simulator for `PUT /api/charging`. It accepts host, port, API key, and interval; sends typed JSON; increments progress from 20% to 100%; and handles HTTP/network errors without exiting.

#### `raspberry-pi/requirements.txt`

Declares bounded compatible ranges for FastAPI, Uvicorn, Pydantic, pymavlink, pyserial, and dotenv. Only pymavlink is required by the current forwarder, while the broader list supports future Pi-side API work.

### Existing documentation

#### `DRONE_TEAM_INTERFACE.md`

Defines ownership between the drone/network team and dashboard team, required MAVLink 2 transport and message types, recommended rate, and an acceptance test for connected/disconnected behavior.

#### `NETWORK_SETUP.md`

Documents Pixhawk-to-Pi forwarding, hotspot and Tailscale addressing, firewall requirements, charging simulation, backend target configuration, and operational commands. It references `.env.example` files that are not currently present in the repository; adding sanitized examples is recommended.

## 9. Database schema

### `users`

| Column | Type | Constraints |
|---|---|---|
| `id` | Integer | Primary key, indexed |
| `name` | String(100) | Not null |
| `email` | String(255) | Unique, indexed, not null |
| `hashed_password` | String(255) | Not null |
| `created_at` | DateTime | Defaults to current UTC time |

### `telemetry_log`

| Column | Purpose |
|---|---|
| `id` | Primary key |
| `drone_id`, `sysid` | Drone identity |
| `lat`, `lng`, `alt`, `heading`, `speed` | Navigation snapshot |
| `battery_pct` | Battery snapshot |
| `flight_mode`, `armed` | Vehicle state |
| `logged_at` | Record timestamp |

## 10. Diagnostics and operations

### Verify raw UDP traffic

Stop the backend and any other UDP/14550 listener, then run:

```powershell
.\venv\Scripts\python.exe udp_probe.py --host 0.0.0.0 --port 14550
```

### Verify MAVLink decoding

```powershell
.\venv\Scripts\python.exe mavlink_probe.py udpin:0.0.0.0:14550 --summary-every 5
```

### Run tests

```powershell
.\venv\Scripts\python.exe -m unittest discover -v
```

Current observed result: nine tests discovered, five passing, two failing, and two erroring because assertions/interfaces are stale after model evolution.

### Simulate charging

```powershell
python raspberry-pi\charging_station_simulator.py --host 127.0.0.1 --port 8000
```

Add `--api-key VALUE` when `DASHBOARD_API_KEY` is configured.

## 11. Security notes

- Treat the current JWT secret and PostgreSQL password as exposed development credentials and rotate them.
- Move secrets to `.env`, keep `.env` ignored, and provide only `.env.example` templates.
- The localStorage presence check is not authorization. Validate bearer JWTs on protected backend routes.
- Prefer short-lived access tokens, refresh strategy, logout, rate limiting, and password policy.
- Restrict CORS in production.
- Use TLS (`https`/`wss`) outside a trusted development network.
- Avoid putting long-lived API keys in WebSocket URLs because URLs may be logged.

## 12. Known issues and technical debt

1. Authentication currently uses port 8002 while telemetry JavaScript uses port 8000.
2. JWT presence is checked client-side, but telemetry routes do not validate it.
3. JWT secret and database connection details are hard-coded.
4. CORS is wildcarded for development.
5. Four unit tests are stale.
6. `_fleet_manager.py` duplicates active code and is not independently runnable.
7. Fleet battery handling currently covers `SYS_STATUS` but not the richer `BATTERY_STATUS` path used by `DroneConnection`.
8. Fleet `connectionType` is hard-coded to `COM`, even for UDP targets.
9. The disconnected fallback is included in fleet results, so fleet summary can report one active item while no physical drone is active.
10. `TelemetryLog` is not written.
11. Home and charging-station map coordinates are hard-coded sample values.
12. Some source strings show encoding artifacts (`Â`, `â`, or mojibake icons).
13. Dependency lines are duplicated and `.env.example` files referenced by documentation are absent.
14. There is no automated browser or end-to-end test suite.

## 13. Recommended refactoring sequence

1. Create one environment-driven `BACKEND_URL` used by login, REST, and WebSockets.
2. Move all secrets and database settings into configuration and add sanitized examples.
3. Add JWT bearer validation to protected API and WebSocket connections.
4. Extract `FleetManager` into one tested module and delete the duplicate implementation.
5. Unify single-drone and fleet normalization so both process every supported MAVLink message consistently.
6. Correct disconnected fleet summary semantics and transport reporting.
7. Update all unit tests and add API/WebSocket integration tests.
8. Add Alembic migrations and optional telemetry persistence.
9. Normalize UTF-8 source encoding and remove unused assets/dependencies.
10. Add a production launch method, TLS/reverse proxy, structured logs, and monitoring.

## 14. Handoff checklist

- [ ] Replace bracketed fields in the internship report.
- [ ] Rotate and externalize database/JWT secrets.
- [ ] Create `drone_dashboard` PostgreSQL database.
- [ ] Install backend dependencies in the project virtual environment.
- [ ] Confirm configured COM ports and/or UDP target.
- [ ] Allow UDP 14550 and the selected HTTP port through Windows Firewall.
- [ ] Start one canonical backend port and update all frontend URLs to match.
- [ ] Serve the frontend through HTTP.
- [ ] Verify root health, signup/login, REST status, and both WebSockets.
- [ ] Run raw UDP and MAVLink probes before hardware troubleshooting.
- [ ] Update the four stale tests before using CI as a release gate.

