"""Main window.

Assembles the ROI/image-view panel (gui/roi_panel.py) - the experiment-
control panel reused from lspr_acq_shell, real device wiring, and a
sweep-pipeline "start experiment" flow are later milestones (see the
architecture plan's section 12 delivery checklist), not built here yet.

Shows one real (simulated) frame at startup so the panel isn't an empty box
- SimulatedCamera is an already-tested v1 code path, not a shortcut around
real device wiring; this deliberately does NOT start a live sweep loop
(that needs the not-yet-built experiment-control flow), just proves the
image view + ROI overlay work end to end against real frame data.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lspr_ui import app_icon

from lspri_acq_app import __version__
from lspri_acq_app.device.camera_base import CameraSettings
from lspri_acq_app.device.simulated_camera import SimulatedCamera
from lspri_acq_app.gui.roi_panel import RoiPanel
from lspri_acq_app.version import APP_NAME


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1000, 700)

        header = QLabel(f"{APP_NAME}  —  ver. {__version__}", self)
        header.setStyleSheet("font-size: 14px; font-weight: 600; padding: 4px 0px;")

        self.roi_panel = RoiPanel(self)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.addWidget(header)
        layout.addWidget(self.roi_panel, 1)
        self.setLayout(layout)

        self._show_startup_preview()

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
