"""Main window.

Assembles the ROI/image-view panel (gui/roi_panel.py), the illumination/
camera per-wavelength settings panel (gui/illumination_camera_settings_panel.py,
2026-08-09 - the first piece of the session-recording workflow with a real
GUI; see that module's docstring for what's v1-scoped vs. deferred), and the
pump/valve/selector experiment-control panel (gui/experiment_control_window.py,
reused from lspr_acq_shell - see that module's docstring for what's shared
vs. simplified in this first working version). Real camera/illumination
device wiring and a sweep-pipeline "start experiment" flow that ties the
image acquisition to the experiment-control plan are later milestones (see
the architecture plan's section 12 delivery checklist), not built here yet -
this window currently runs the pump/valve/selector plan and the camera
preview as independent panels, not yet synchronized, and the settings panel
doesn't drive a running sweep yet either.

Shows one real (simulated) frame at startup so the ROI panel isn't an empty
box - SimulatedCamera is an already-tested v1 code path, not a shortcut
around real device wiring; this deliberately does NOT start a live sweep
loop (that needs the not-yet-built sweep-pipeline integration), just proves
the image view + ROI overlay work end to end against real frame data.

Save/Load Session (2026-08-09) writes/reads a v6.4 HDF5 file via
storage/hdf5_export.py - illumination/camera settings, ROI definitions, and
the experiment-control window's valve/switch/color-palette state, i.e.
everything that currently HAS a GUI to set it. Does not yet touch the plan
table itself (already has its own im/export path, see hdf5_export.py's
module docstring) or start/record a live sweep (SweepPipeline isn't wired to
this window at all yet) - save_session()/load_session() are plain methods,
kept separate from the button handlers that drive the file dialog, so they
can be exercised directly in tests without a real dialog.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt

from lspr_ui import app_icon

from lspri_acq_app import __version__
from lspri_acq_app.device.camera_base import CameraSettings
from lspri_acq_app.device.simulated_camera import SimulatedCamera
from lspri_acq_app.gui.experiment_control_window import ExperimentControlWindow
from lspri_acq_app.gui.illumination_camera_settings_panel import IlluminationCameraSettingsPanel
from lspri_acq_app.gui.roi_panel import RoiPanel
from lspri_acq_app.storage.hdf5_export import ImagingMeasurementWriter, read_imaging_session
from lspri_acq_app.version import APP_NAME


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1300, 800)

        header = QLabel(f"{APP_NAME}  —  ver. {__version__}", self)
        header.setStyleSheet("font-size: 14px; font-weight: 600; padding: 4px 0px;")

        save_session_button = QPushButton("Save Session...", self)
        save_session_button.clicked.connect(self._on_save_session_clicked)
        load_session_button = QPushButton("Load Session...", self)
        load_session_button.clicked.connect(self._on_load_session_clicked)

        header_row = QHBoxLayout()
        header_row.addWidget(header)
        header_row.addStretch(1)
        header_row.addWidget(load_session_button)
        header_row.addWidget(save_session_button)

        self.roi_panel = RoiPanel(self)
        self.illumination_camera_settings_panel = IlluminationCameraSettingsPanel(self)
        self.experiment_control_window = ExperimentControlWindow(self)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self.roi_panel)
        splitter.addWidget(self.illumination_camera_settings_panel)
        splitter.addWidget(self.experiment_control_window)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.addLayout(header_row)
        layout.addWidget(splitter, 1)
        self.setLayout(layout)

        self._show_startup_preview()

    # -- session save/restore -------------------------------------------------

    def save_session(self, path: str | Path) -> None:
        writer = ImagingMeasurementWriter(Path(path))
        try:
            settings = self.illumination_camera_settings_panel.current_settings()
            writer.write_illumination_settings(settings)
            writer.write_camera_settings(settings)
            writer.write_roi_definitions(self.roi_panel.rois())
            state = self.experiment_control_window.assignment_table_state()
            writer.write_valve_state_labels(state["valve_state_labels"], state["valve_state_colors"])
            writer.write_color_palette_entries(state["color_palette_entries"])
            writer.write_switch_solution_labels(state["switch_solution_labels"])
        finally:
            writer.close()

    def load_session(self, path: str | Path) -> None:
        snapshot = read_imaging_session(Path(path))
        self.illumination_camera_settings_panel.load_settings(snapshot.settings)
        self.roi_panel.load_rois(snapshot.rois)
        self.experiment_control_window.apply_assignment_table_state(
            valve_state_labels=snapshot.valve_state_labels,
            valve_state_colors=snapshot.valve_state_colors,
            color_palette_entries=snapshot.color_palette_entries,
            switch_solution_labels=snapshot.switch_solution_labels,
        )

    def _on_save_session_clicked(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "Save Session", "", "HDF5 session files (*.h5)")
        if path:
            self.save_session(path)

    def _on_load_session_clicked(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Load Session", "", "HDF5 session files (*.h5)")
        if path:
            self.load_session(path)

    def _show_startup_preview(self) -> None:
        camera = SimulatedCamera(
            width_px=640,
            height_px=480,
            spot_centers_px=((220.0, 240.0), (420.0, 240.0)),
            noise_std=8.0,
        )
        camera.open()
        camera.configure(CameraSettings(exposure_us=10_000.0))
        frame = camera.acquire_frame(timeout_ms=1000)
        camera.close()

        self.roi_panel.set_image_shape(frame.image.shape)
        self.roi_panel.show_frame(frame.image)
        self.roi_panel.add_roi(220.0, 240.0)
        self.roi_panel.add_roi(420.0, 240.0)
