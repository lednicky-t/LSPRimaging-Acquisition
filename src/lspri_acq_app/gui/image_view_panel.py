"""Live image display panel - latest-frame-of-latest-cube only, per the
architecture plan's section 8/10 rule: this widget only ever shows whatever
was most recently handed to it via show_frame() - it never blocks on, or
pulls from, the processing/save queues itself.

Display orientation follows the exact convention already validated in
spikes/lspri_acq_phase0/benchmark_ui.py: setImage() is called with the
transpose of a (height, width) numpy array, so pyqtgraph's native (x, y)
plot coordinates line up with conventional image (column, row) coordinates
- the same convention AreaRoi.center_x/center_y and processing/
roi_extraction.py already use. ROI overlay items (roi_panel.py) depend on
this alignment - keep the transpose if this panel's display logic changes.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QVBoxLayout, QWidget


class ImageViewPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image_view = pg.ImageView()
        # Line-profile ROI tool and the settings/export menu aren't part of
        # v1's scope (manual sample/reference ROI placement lives in
        # roi_panel.py instead) - hidden to keep the panel focused, matching
        # the same hide-these-two pattern already used in the Phase 0 spike.
        self._image_view.ui.roiBtn.hide()
        self._image_view.ui.menuBtn.hide()
        self._has_shown_a_frame = False

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._image_view)
        self.setLayout(layout)

    @property
    def view_box(self) -> pg.ViewBox:
        """The underlying pyqtgraph ViewBox - roi_panel.py adds ROI overlay
        items directly here, since pg.ImageView has no "add an arbitrary
        overlay item" method of its own beyond exposing the view."""
        return self._image_view.getView()

    def show_frame(self, image: np.ndarray) -> None:
        """image: shape (height, width). Auto-levels/auto-ranges only on
        the very first frame shown, so the user's own zoom/pan/level
        adjustments survive subsequent live updates."""
        self._image_view.setImage(
            image.T,
            autoLevels=not self._has_shown_a_frame,
            autoRange=not self._has_shown_a_frame,
        )
        self._has_shown_a_frame = True

    def clear(self) -> None:
        self._image_view.clear()
        self._has_shown_a_frame = False
