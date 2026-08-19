"""Raw UDP probe that does not attempt MAVLink decoding.

"""

from __future__ import annotations

import argparse
import socket
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=14550)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(1.0)

    count = 0
    byte_count = 0
    print(f"Raw UDP listener active on {args.host}:{args.port}")
    print("Press Ctrl+C to stop")

    try:
        while True:
            try:
                payload, sender = sock.recvfrom(65535)
            except socket.timeout:
                print("No UDP datagram in the last second")
                continue

            count += 1
            byte_count += len(payload)
            prefix = payload[:16].hex(" ")
            print(
                f"datagram={count} bytes={len(payload)} from={sender[0]}:{sender[1]} "
                f"first16={prefix}"
            )
    except KeyboardInterrupt:
        print(f"Received {count} datagrams / {byte_count} bytes")
    finally:
        sock.close()


if __name__ == "__main__":
    main()