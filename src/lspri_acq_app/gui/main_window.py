"""Main window - early scaffold.

Deliberately minimal today: the device layer, sweep pipeline, and real GUI
panels (image view, ROI panel, experiment control) are later Phase 2
milestones (see the architecture plan's section 12 delivery checklist). This
window exists to prove the app boots and wires up lspr_core/lspr_io/lspr_ui/
lspr_acq_shell correctly, not to be feature-complete.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lspr_ui import app_icon

from lspri_acq_app import __version__
from lspri_acq_app.version import APP_NAME


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(720, 480)

        title = QLabel(APP_NAME, self)
        title.setStyleSheet("font-size: 20px; font-weight: 700;")

        version_label = QLabel(f"ver. {__version__}", self)

        status = QLabel(
            "Early scaffold - camera/illumination device layer, sweep\n"
            "pipeline, and imaging GUI panels are still under construction.\n"
            "See docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md\n"
            "in the umbrella repo for the delivery-milestones checklist.",
            self,
        )
        status.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(version_label)
        layout.addSpacing(16)
        layout.addWidget(status, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        self.setLayout(layout)
