import unittest

from drone_connection import DroneConnection


class Message:
    def __init__(self, message_type, **fields):
        self.message_type = message_type
        for name, value in fields.items():
            setattr(self, name, value)

    def get_type(self):
        return self.message_type


class BatteryProcessingTests(unittest.TestCase):
    def setUp(self):
        self.drone = DroneConnection()

    def test_sys_status_uses_controller_battery_values(self):
        self.drone._process_battery(Message(
            'SYS_STATUS', voltage_battery=22100, current_battery=1860,
            battery_remaining=25,
        ))
        self.assertEqual(self.drone.telemetry['battery'], {
            'voltage': 22.1, 'current': 18.6, 'percent': 25,
        })

    def test_battery_status_sums_cell_voltages(self):
        self.drone._process_battery(Message(
            'BATTERY_STATUS',
            voltages=[3700, 3700, 3700, 3700, 3700, 3700, 65535, 65535, 65535, 65535],
            current_battery=1000, battery_remaining=25,
        ))
        self.assertEqual(self.drone.telemetry['battery']['voltage'], 22.2)
        self.assertEqual(self.drone.telemetry['battery']['percent'], 25)

    def test_unavailable_values_do_not_erase_last_reading(self):
        self.drone.telemetry['battery'] = {'voltage': 22.2, 'current': 10.0, 'percent': 25}
        self.drone._process_battery(Message(
            'SYS_STATUS', voltage_battery=65535, current_battery=-1,
            battery_remaining=-1,
        ))
        self.assertEqual(self.drone.telemetry['battery'], {
            'voltage': 22.2, 'current': 10.0, 'percent': 25,
        })


if __name__ == '__main__':
    unittest.main()
