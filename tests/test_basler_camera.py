"""Tests for the Basler camera driver that don't need a physical camera
attached - real pypylon calls, exercising the "no device found"/"bad serial
number" paths, which are themselves real, meaningful behavior (not mocked
around). See basler_camera.py's module docstring for what IS and is NOT
verified against real hardware yet.
"""

from __future__ import annotations

import unittest

from lspri_acq_app.device.basler_camera import BaslerCamera, discover_basler_cameras
from lspri_acq_app.device.camera_base import CameraError


class DiscoverBaslerCamerasTests(unittest.TestCase):
    def test_returns_a_list_without_raising(self) -> None:
        # Real pypylon enumeration call - returns [] on a machine with no
        # Basler camera attached, which is the case in CI/this environment.
        # Not mocked: proves the driver's discovery call actually works
        # against the installed pypylon, not just that it type-checks.
        devices = discover_basler_cameras()
        self.assertIsInstance(devices, list)


class BaslerCameraTests(unittest.TestCase):
    def test_open_with_unknown_serial_number_raises_camera_error(self) -> None:
        camera = BaslerCamera(serial_number="nonexistent-serial-does-not-exist")
        with self.assertRaises(CameraError):
            camera.open()

    def test_is_connected_false_before_open(self) -> None:
        camera = BaslerCamera(serial_number="nonexistent-serial-does-not-exist")
        self.assertFalse(camera.is_connected())

    def test_close_before_open_is_a_no_op(self) -> None:
        camera = BaslerCamera(serial_number="nonexistent-serial-does-not-exist")
        camera.close()  # must not raise
        self.assertFalse(camera.is_connected())

    def test_device_name_falls_back_to_serial_number_before_open(self) -> None:
        camera = BaslerCamera(serial_number="ABC123")
        self.assertIn("ABC123", camera.device_name())

    def test_claim_owner_is_per_instance(self) -> None:
        first = BaslerCamera(serial_number="A")
        second = BaslerCamera(serial_number="A")
        self.assertNotEqual(first._claim_owner, second._claim_owner)
        self.assertTrue(first._claim_owner.startswith("basler:"))


if __name__ == "__main__":
    unittest.main()
