"""Tests for device/registry.py's registration side effects - importing it
wires CAMERA into lspr_acq_shell's shared device family registry (see the
module's own docstring).

Deliberately does NOT construct a real DeviceLifecycleController/
DeviceCommunicationService here: those touch real settings files and, via
run_full_cycle()'s PUMP/SWITCH/SELECTOR family scan, real serial ports on
whatever machine runs this test - out of scope for a device-registration
unit test and not something to risk poking at. _discover_and_connect_camera
is exercised directly instead, which is safe because with no Basler camera
attached (the real, current state of this environment) it returns before
ever touching the controller argument.
"""

from __future__ import annotations

import unittest

from lspr_acq_shell.device_lifecycle import STAGE_MISSING, device_family_order

from lspri_acq_app.device import registry  # noqa: F401 - import triggers registration


class DeviceRegistryTests(unittest.TestCase):
    def test_camera_family_is_registered(self) -> None:
        self.assertIn(registry.CAMERA, device_family_order())

    def test_discover_and_connect_reports_missing_with_no_camera_attached(self) -> None:
        events: list = []

        event = registry._discover_and_connect_camera(None, [], events.append)

        self.assertEqual(event.device_key, registry.CAMERA)
        self.assertEqual(event.stage, STAGE_MISSING)
        self.assertFalse(event.connected)
        self.assertEqual(events, [event])


if __name__ == "__main__":
    unittest.main()
