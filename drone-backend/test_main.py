import unittest
from unittest.mock import patch

import main


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


if __name__ == "__main__":
    unittest.main()
