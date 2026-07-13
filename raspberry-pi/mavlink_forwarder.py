"""Forward raw MAVLink frames from a Pixhawk to one or more UDP receivers."""

import os
import socket
import time

from pymavlink import mavutil


PIXHAWK_DEVICE = os.getenv("PIXHAWK_DEVICE", "/dev/ttyACM0")
PIXHAWK_BAUD = int(os.getenv("PIXHAWK_BAUD", "115200"))
UDP_TARGETS = os.getenv("UDP_TARGETS", "192.168.137.1:14550")


def parse_targets(value):
    targets = []
    for item in value.split(","):
        host, port = item.strip().rsplit(":", 1)
        targets.append((host, int(port)))
    return targets


def run():
    targets = parse_targets(UDP_TARGETS)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    while True:
        connection = None
        try:
            print(f"Opening Pixhawk at {PIXHAWK_DEVICE} ({PIXHAWK_BAUD} baud)")
            connection = mavutil.mavlink_connection(PIXHAWK_DEVICE, baud=PIXHAWK_BAUD)
            if connection.wait_heartbeat(timeout=10) is None:
                raise TimeoutError("Pixhawk heartbeat timed out")
            print(f"Forwarding MAVLink to {targets}")

            while True:
                message = connection.recv_msg()
                if message is None:
                    time.sleep(0.005)
                    continue
                frame = message.get_msgbuf()
                for target in targets:
                    sock.sendto(frame, target)
        except (OSError, TimeoutError) as error:
            print(f"Forwarder disconnected: {error}; retrying in 3 seconds")
            time.sleep(3)
        finally:
            if connection is not None:
                connection.close()


if __name__ == "__main__":
    run()
