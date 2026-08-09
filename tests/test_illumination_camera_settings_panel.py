"""Tests for gui/illumination_camera_settings_panel.py's
IlluminationCameraSettingsPanel - real QTableWidget/QDoubleSpinBox/QComboBox
widgets, not mocks, matching this app's existing real-widget test
convention (test_plan_table_model.py, test_experiment_control_window.py)."""

from __future__ import annotations

import unittest

from PyQt6.QtWidgets import QApplication

from lspri_acq_app.domain.models import (
    ImagingAcquisitionSettings,
    WavelengthCameraSettings,
    WavelengthIlluminationSettings,
)
from lspri_acq_app.gui.illumination_camera_settings_panel import IlluminationCameraSettingsPanel

_APP = QApplication.instance() or QApplication([])


class RowManagementTests(unittest.TestCase):
    def test_add_wavelength_row_increases_row_count(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        panel.add_wavelength_row(450.0)
        panel.add_wavelength_row(500.0)
        self.assertEqual(panel.row_count(), 2)

    def test_remove_row_decreases_row_count(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        panel.add_wavelength_row(450.0)
        panel.remove_row(0)
        self.assertEqual(panel.row_count(), 0)

    def test_clear_rows_empties_the_table(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        panel.add_wavelength_row(450.0)
        panel.add_wavelength_row(500.0)
        panel.clear_rows()
        self.assertEqual(panel.row_count(), 0)


class CurrentSettingsTests(unittest.TestCase):
    def test_empty_table_produces_empty_settings(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        settings = panel.current_settings()
        self.assertEqual(settings.wavelengths_nm, [])

    def test_defaults_produce_unset_gain_settle_and_current(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        panel.add_wavelength_row(450.0)
        settings = panel.current_settings()

        self.assertEqual(settings.wavelengths_nm, [450.0])
        camera = settings.camera_settings_by_wavelength[450.0]
        illumination = settings.illumination_settings_by_wavelength[450.0]
        self.assertIsNone(camera.gain)
        self.assertIsNone(illumination.settle_time_ms)
        self.assertIsNone(illumination.current)
        self.assertEqual(illumination.spectrum_source, "default_file")

    def test_explicit_values_are_carried_through(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        panel.add_wavelength_row(
            500.0,
            exposure_us=9000.0,
            gain=3.0,
            binning=2,
            settle_time_ms=25.0,
            current=1.5,
            spectrum_source="measured",
        )
        settings = panel.current_settings()

        camera = settings.camera_settings_by_wavelength[500.0]
        illumination = settings.illumination_settings_by_wavelength[500.0]
        self.assertEqual((camera.exposure_us, camera.gain, camera.binning), (9000.0, 3.0, 2))
        self.assertEqual((illumination.settle_time_ms, illumination.current), (25.0, 1.5))
        self.assertEqual(illumination.spectrum_source, "measured")

    def test_global_fallback_fields_mirror_the_first_row(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        panel.add_wavelength_row(450.0, exposure_us=1000.0, gain=2.0)
        panel.add_wavelength_row(500.0, exposure_us=9000.0, gain=3.0)
        settings = panel.current_settings()

        self.assertEqual(settings.exposure_us, 1000.0)
        self.assertEqual(settings.gain, 2.0)

    def test_multiple_rows_each_get_their_own_override(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        panel.add_wavelength_row(450.0, exposure_us=1000.0)
        panel.add_wavelength_row(500.0, exposure_us=9000.0)
        settings = panel.current_settings()

        self.assertEqual(settings.camera_settings_by_wavelength[450.0].exposure_us, 1000.0)
        self.assertEqual(settings.camera_settings_by_wavelength[500.0].exposure_us, 9000.0)


class LoadSettingsRoundTripTests(unittest.TestCase):
    def test_load_settings_repopulates_the_table(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0],
            exposure_us=1000.0,
            gain=2.0,
            camera_settings_by_wavelength={
                500.0: WavelengthCameraSettings(exposure_us=9000.0, gain=3.0, binning=2)
            },
            illumination_settings_by_wavelength={
                500.0: WavelengthIlluminationSettings(settle_time_ms=25.0, current=1.5, spectrum_source="measured")
            },
        )

        panel.load_settings(settings)

        self.assertEqual(panel.row_count(), 2)
        round_tripped = panel.current_settings()
        self.assertEqual(round_tripped.wavelengths_nm, [450.0, 500.0])
        second = round_tripped.camera_settings_by_wavelength[500.0]
        self.assertEqual((second.exposure_us, second.gain, second.binning), (9000.0, 3.0, 2))
        second_illum = round_tripped.illumination_settings_by_wavelength[500.0]
        self.assertEqual((second_illum.settle_time_ms, second_illum.current), (25.0, 1.5))

    def test_load_settings_clears_previous_rows_first(self) -> None:
        panel = IlluminationCameraSettingsPanel()
        panel.add_wavelength_row(600.0)
        panel.load_settings(ImagingAcquisitionSettings(wavelengths_nm=[450.0], exposure_us=1000.0))
        self.assertEqual(panel.row_count(), 1)
        self.assertEqual(panel.current_settings().wavelengths_nm, [450.0])


if __name__ == "__main__":
    unittest.main()
