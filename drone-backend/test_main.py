import unittest
from unittest.mock import patch

import main
from fastapi import HTTPException


class TelemetryTests(unittest.TestCase):
    def test_disconnected_payload_does_not_mutate_template(self):
        original_timestamp = main.DISCONNECTED_TELEMETRY_TEMPLATE["communication"]["lastUpdate"]

        with patch.object(main.drone, "is_connected_to_drone", return_value=False):
            payload = main.get_telemetry_data()

        self.assertEqual(payload["connectionStatus"], "Disconnected")
        self.assertTrue(payload["communication"]["lastUpdate"])
        self.assertEqual(
            main.DISCONNECTED_TELEMETRY_TEMPLATE["communication"]["lastUpdate"],
            original_timestamp,
        )
        self.assertIsNot(payload["communication"], main.DISCONNECTED_TELEMETRY_TEMPLATE["communication"])

    def test_transport_names(self):
        self.assertEqual(main.drone._transport_name("COM9"), "USB Serial")
        self.assertEqual(main.drone._transport_name("udpin:0.0.0.0:14550"), "Wi-Fi/Cellular UDP")
        self.assertEqual(main.drone._transport_name("tcp:100.64.0.2:5760"), "Network TCP")

    def test_home_distance_uses_geographic_coordinates(self):
        distance = main.drone._distance_metres(37.0, -122.0, 37.001, -122.0)
        self.assertAlmostEqual(distance, 111.2, delta=0.5)

    def test_api_key_rejected_when_configured(self):
        with patch.object(main, "API_KEY", "secret"):
            with self.assertRaises(HTTPException):
                main.require_api_key("wrong")
            self.assertIsNone(main.require_api_key("secret"))


if __name__ == "__main__":
    unittest.main()
