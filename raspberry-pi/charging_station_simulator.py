"""Simulate a charging-station Pi sending updates to the dashboard backend."""

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def send_update(url, api_key, payload):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="PUT",
    )
    with urlopen(request, timeout=5) as response:
        return response.status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.137.1", help="Laptop hotspot IP")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/api/charging"
    progress = 20.0
    print(f"Sending simulated station telemetry to {url}")
    while True:
        payload = {
            "docked": True,
            "status": "Complete" if progress >= 100 else "Charging",
            "progress": progress,
            "voltage": 25.2,
            "current": 0.0 if progress >= 100 else 4.2,
            "eta": max(0.0, (100.0 - progress) / 2.0),
        }
        try:
            status = send_update(url, args.api_key, payload)
            print(f"HTTP {status}: {payload}")
            progress = min(100.0, progress + 1.0)
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"Update failed: {exc}")
        time.sleep(max(0.1, args.interval))


if __name__ == "__main__":
    main()
