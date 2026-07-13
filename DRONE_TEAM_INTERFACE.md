# Dashboard telemetry interface

The drone team owns delivery of MAVLink telemetry to the dashboard host. The dashboard is independent of the Raspberry Pi, hotspot, cellular modem, and VPN implementation.

## Required handoff

- MAVLink 2 over UDP to dashboard port 14550
- Recommended update rate: 10–20 Hz
- Required messages: `HEARTBEAT`, `GLOBAL_POSITION_INT`, `GPS_RAW_INT`, `ATTITUDE`, `SYS_STATUS` or `BATTERY_STATUS`, `VFR_HUD`, and `HOME_POSITION`
- Optional message: `RADIO_STATUS`

The drone team provides the reachable network path and confirms packet delivery. The dashboard team owns parsing, stale-data detection, API/WebSocket delivery, authentication, and presentation.

## Acceptance test

The interface is accepted when the required messages arrive, the dashboard reports `Connected`, values update, and stopping the stream produces `Disconnected` within the configured timeout.
