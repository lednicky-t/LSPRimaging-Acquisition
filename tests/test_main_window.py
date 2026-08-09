"""Qt widget tests for gui/main_window.py - construction and the
save_session()/load_session() methods (2026-08-09). Button-click ->
QFileDialog wiring isn't exercised here (a real native file dialog can't run
headlessly); save_session()/load_session() are plain methods precisely so
this round trip can be tested without one.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from lspri_acq_app.gui.experiment_control_window import ExperimentControlWindow
from lspri_acq_app.gui.main_window import MainWindow

_APP = QApplication.instance() or QApplication([])


def _close_and_flush(widget) -> None:
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()


class _MainWindowTestCase(unittest.TestCase):
    """Isolates ExperimentControlWindow's settings persistence the same way
    test_experiment_control_window.py's _make_window() does - MainWindow
    constructs a real one, so without this every test here would read/write
    the maintainer's real per-user settings file."""

    def setUp(self) -> None:
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        settings_path = Path(tmp_dir.name) / "lspri_acq_settings.json"
        patcher = patch.object(ExperimentControlWindow, "_settings_path", lambda self: settings_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.window = MainWindow()
        self.addCleanup(_close_and_flush, self.window)

        session_dir = tempfile.TemporaryDirectory()
        self.addCleanup(session_dir.cleanup)
        self.session_path = Path(session_dir.name) / "session.h5"


class ConstructionTests(_MainWindowTestCase):
    def test_embeds_the_settings_panel(self) -> None:
        from lspri_acq_app.gui.illumination_camera_settings_panel import IlluminationCameraSettingsPanel

        self.assertIsInstance(self.window.illumination_camera_settings_panel, IlluminationCameraSettingsPanel)

    def test_startup_preview_adds_two_rois(self) -> None:
        self.assertEqual(len(self.window.roi_panel.rois()), 2)


class SaveLoadSessionRoundTripTests(_MainWindowTestCase):
    def test_full_round_trip_through_all_three_panels(self) -> None:
        self.window.illumination_camera_settings_panel.add_wavelength_row(
            450.0, exposure_us=1000.0, gain=2.0
        )
        self.window.illumination_camera_settings_panel.add_wavelength_row(
            500.0, exposure_us=9000.0, settle_time_ms=25.0
        )
        self.window.experiment_control_window.apply_assignment_table_state(
            valve_state_labels={"Open": "Loaded", "Close": "Waste"},
            color_palette_entries=[("Blue", "#0000FF")],
            switch_solution_labels=["Buffer A"],
        )
        original_rois = self.window.roi_panel.rois()  # the two startup-preview ROIs

        self.window.save_session(self.session_path)

        fresh = MainWindow()
        self.addCleanup(_close_and_flush, fresh)
        fresh.load_session(self.session_path)

        restored_settings = fresh.illumination_camera_settings_panel.current_settings()
        self.assertEqual(restored_settings.wavelengths_nm, [450.0, 500.0])
        self.assertEqual(restored_settings.camera_settings_by_wavelength[450.0].gain, 2.0)
        self.assertEqual(restored_settings.illumination_settings_by_wavelength[500.0].settle_time_ms, 25.0)

        self.assertEqual(
            [roi.area_roi_id for roi in fresh.roi_panel.rois()],
            [roi.area_roi_id for roi in original_rois],
        )

        self.assertEqual(fresh.experiment_control_window._valve_state_label("Open"), "Loaded")
        self.assertEqual(fresh.experiment_control_window.step_color_combo.itemText(0), "Blue")
        self.assertEqual(fresh.experiment_control_window._switch_display_text(1), "1: Buffer A")

    def test_save_session_does_not_leave_the_file_open(self) -> None:
        """A real regression risk with any HDF5 writer: forgetting to close()
        (or only closing on the happy path) leaves the file locked - load
        right after save is the simplest real proof it's actually closed."""
        self.window.save_session(self.session_path)
        read_back = MainWindow()
        self.addCleanup(_close_and_flush, read_back)
        read_back.load_session(self.session_path)  # must not raise / hang


if __name__ == "__main__":
    unittest.main()
