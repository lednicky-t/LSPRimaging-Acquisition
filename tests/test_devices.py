from __future__ import annotations

import math
import unittest

from lspri_acq_app.device.camera_base import CameraCapabilities, CameraSettings
from lspri_acq_app.device.simulated_camera import SimulatedCamera
from lspri_acq_app.device.simulated_illumination import SimulatedIllumination


class SimulatedCameraTests(unittest.TestCase):
    def test_acquire_frame_requires_open(self) -> None:
        camera = SimulatedCamera()
        with self.assertRaises(RuntimeError):
            camera.acquire_frame(timeout_ms=100)

    def test_acquire_frame_shape_and_metadata(self) -> None:
        camera = SimulatedCamera(width_px=64, height_px=48)
        camera.open()
        camera.configure(CameraSettings(exposure_us=5000.0, gain=2.0, pixel_format="Mono8", binning=2))
        frame = camera.acquire_frame(timeout_ms=100)

        self.assertEqual(frame.image.shape, (48, 64))
        self.assertTrue(math.isnan(frame.wavelength_nm))
        self.assertEqual(frame.metadata["exposure_us"], 5000.0)
        self.assertEqual(frame.metadata["gain"], 2.0)
        self.assertEqual(frame.metadata["pixel_format"], "Mono8")
        self.assertEqual(frame.metadata["binning"], 2)

    def test_frame_peaks_near_configured_spot_center(self) -> None:
        camera = SimulatedCamera(
            width_px=100,
            height_px=100,
            spot_centers_px=((25.0, 75.0),),
            noise_std=0.0,
        )
        camera.open()
        frame = camera.acquire_frame(timeout_ms=100)

        peak_row, peak_col = divmod(int(frame.image.argmax()), frame.image.shape[1])
        self.assertAlmostEqual(peak_col, 25, delta=2)
        self.assertAlmostEqual(peak_row, 75, delta=2)

    def test_capabilities_reports_configured_sensor_size(self) -> None:
        camera = SimulatedCamera(width_px=320, height_px=240)
        capabilities = camera.capabilities()
        self.assertIsInstance(capabilities, CameraCapabilities)
        self.assertEqual(capabilities.sensor_width_px, 320)
        self.assertEqual(capabilities.sensor_height_px, 240)


class SimulatedIlluminationTests(unittest.TestCase):
    def test_set_wavelength_requires_open(self) -> None:
        illumination = SimulatedIllumination()
        with self.assertRaises(RuntimeError):
            illumination.set_wavelength(500.0)

    def test_set_wavelength_within_range(self) -> None:
        illumination = SimulatedIllumination(wavelength_range_nm=(400.0, 700.0))
        illumination.open()
        self.assertIsNone(illumination.current_wavelength())

        illumination.set_wavelength(550.0)
        self.assertEqual(illumination.current_wavelength(), 550.0)

    def test_set_wavelength_outside_range_raises(self) -> None:
        illumination = SimulatedIllumination(wavelength_range_nm=(400.0, 700.0))
        illumination.open()
        with self.assertRaises(ValueError):
            illumination.set_wavelength(720.0)

    def test_settle_time_is_zero(self) -> None:
        illumination = SimulatedIllumination()
        self.assertEqual(illumination.settle_time_ms(), 0.0)

    def test_close_clears_current_wavelength(self) -> None:
        illumination = SimulatedIllumination()
        illumination.open()
        illumination.set_wavelength(450.0)
        illumination.close()
        self.assertIsNone(illumination.current_wavelength())


if __name__ == "__main__":
    unittest.main()
