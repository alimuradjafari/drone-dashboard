"""Minimal MAVLink packet probe for diagnosing UDP routing.

Close the FastAPI backend, Mission Planner, QGroundControl, and any other local
listener on the selected UDP port before running this script.
"""

from __future__ import annotations

from collections import Counter
import argparse
import time

from pymavlink import mavutil


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "connection",
        nargs="?",
        default="udpin:0.0.0.0:14550",
        help="pymavlink connection string",
    )
    parser.add_argument(
        "--summary-every",
        type=float,
        default=5.0,
        help="seconds between count summaries",
    )
    args = parser.parse_args()

    link = mavutil.mavlink_connection(
        args.connection,
        source_system=255,
        source_component=190,
    )
    counts: Counter[str] = Counter()
    bad_data = 0
    last_summary = time.monotonic()

    print(f"Listening on {args.connection}")
    print("Press Ctrl+C to stop")

    try:
        while True:
            message = link.recv_match(blocking=True, timeout=1.0)
            if message is None:
                print("No decoded MAVLink packet in the last second")
                continue

            message_type = message.get_type()
            if message_type == "BAD_DATA":
                bad_data += 1
            else:
                counts[message_type] += 1
                print(
                    f"{message_type:28} "
                    f"sys={message.get_srcSystem():3} "
                    f"comp={message.get_srcComponent():3} "
                    f"count={counts[message_type]}"
                )

            if time.monotonic() - last_summary >= args.summary_every:
                print(f"Summary: {dict(counts)}; BAD_DATA={bad_data}")
                last_summary = time.monotonic()
    except KeyboardInterrupt:
        print(f"Final summary: {dict(counts)}; BAD_DATA={bad_data}")
    finally:
        link.close()


if __name__ == "__main__":
    main()