# Raspberry Pi network telemetry

The Pi reads MAVLink from the Pixhawk and forwards the original MAVLink frames to the laptop over UDP. The laptop backend listens on UDP port 14550 and falls back to local `COM9` when configured.

## Raspberry Pi

Connect the Pixhawk by USB and confirm its device name (normally `/dev/ttyACM0`). Copy `raspberry-pi/.env.example` to `.env`, then set `UDP_TARGETS`.

- Windows hotspot: use the laptop address visible to the Pi, commonly `192.168.137.1`.
- Cellular: install Tailscale on both machines and use the laptop's Tailscale `100.x.x.x` address.
- Multiple paths may be comma-separated.

Run on the Pi:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
set -a; . ./.env; set +a
venv/bin/python mavlink_forwarder.py
```

Allow UDP port 14550 through the laptop firewall. Cellular providers commonly use CGNAT, so use Tailscale (or another private VPN) instead of exposing this UDP port publicly.

## Laptop backend

Copy `drone-backend/.env.example` to `drone-backend/.env`. The default connection order is:

```env
MAVLINK_CONNECTIONS=udpin:0.0.0.0:14550,COM9
```

Start the backend from `drone-backend` so its `.env` is loaded. The backend waits five seconds for each connection and rotates between network UDP and local serial until a heartbeat is received.
