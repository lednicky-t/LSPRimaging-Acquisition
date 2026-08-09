"""ROI panel - manual placement/editing only for v1 (no auto-detection, per
the architecture plan's explicit non-goals). Overlays draggable/resizable
pyqtgraph.CircleROI items for each AreaRoi's sample disk on an
ImageViewPanel, plus static (non-interactive) circles marking the
reference annulus, and a side list for add/select/delete and numeric
reference-diameter editing.

Not a port of LSPRimaging Evaluation's ImageInteractionController/
OverlayManager - checked first (see the architecture plan's section 10
correction) and confirmed those are genuinely Qt-coupled to that app's
specific MainWindow with no clean seam; this is a fresh, much smaller
implementation scoped to v1's actual requirement (manual placement/editing,
not eva's auto-detection/chromatic-correction feature set).
"""

from __future__ import annotations

from collections.abc import Callable

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lspri_acq_app.domain.roi import AreaRoi
from lspri_acq_app.domain.roi_editor_tools import move_roi, next_area_roi_id
from lspri_acq_app.gui.image_view_panel import ImageViewPanel

_SAMPLE_PEN = pg.mkPen(color="#f59e0b", width=2)
_REFERENCE_PEN = pg.mkPen(color="#38bdf8", width=1, style=Qt.PenStyle.DashLine)

_DEFAULT_SAMPLE_RADIUS_PX = 10.0
_DEFAULT_REFERENCE_INNER_DIAMETER_PX = 28.0
_DEFAULT_REFERENCE_OUTER_DIAMETER_PX = 36.0


class RoiPanel(QWidget):
    """Combines an ImageViewPanel with ROI add/edit/delete controls.

    Does not own image acquisition - call show_frame()/set_image_shape()
    from whatever's driving live display (main_window.py, eventually).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image_shape: tuple[int, int] | None = None
        self._rois: dict[int, AreaRoi] = {}
        self._sample_items: dict[int, pg.CircleROI] = {}
        self._reference_items: dict[int, tuple[pg.CircleROI, pg.CircleROI]] = {}
        self._on_rois_changed: Callable[[], None] | None = None

        self.image_view = ImageViewPanel(self)

        self._roi_list = QListWidget(self)
        self._roi_list.currentItemChanged.connect(self._on_selection_changed)

        add_button = QPushButton("Add ROI", self)
        add_button.clicked.connect(self._on_add_clicked)
        delete_button = QPushButton("Delete Selected", self)
        delete_button.clicked.connect(self._on_delete_clicked)
        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(delete_button)

        self._reference_inner_spin = QDoubleSpinBox(self)
        self._reference_inner_spin.setRange(0.0, 10000.0)
        self._reference_inner_spin.setSuffix(" px")
        self._reference_inner_spin.valueChanged.connect(self._on_reference_diameters_edited)
        self._reference_outer_spin = QDoubleSpinBox(self)
        self._reference_outer_spin.setRange(0.0, 10000.0)
        self._reference_outer_spin.setSuffix(" px")
        self._reference_outer_spin.valueChanged.connect(self._on_reference_diameters_edited)
        reference_form = QFormLayout()
        reference_form.addRow("Reference inner diameter", self._reference_inner_spin)
        reference_form.addRow("Reference outer diameter", self._reference_outer_spin)
        reference_group = QGroupBox("Selected ROI", self)
        reference_group.setLayout(reference_form)
        self._set_reference_controls_enabled(False)

        side_panel = QVBoxLayout()
        side_panel.addLayout(button_row)
        side_panel.addWidget(self._roi_list, 1)
        side_panel.addWidget(reference_group)
        side_widget = QWidget(self)
        side_widget.setLayout(side_panel)
        side_widget.setMaximumWidth(260)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.image_view, 1)
        layout.addWidget(side_widget)
        self.setLayout(layout)

    # -- external API -----------------------------------------------------------

    def set_on_rois_changed(self, callback: Callable[[], None] | None) -> None:
        """callback fires after any add/move/resize/delete/reference-edit -
        e.g. so a caller can push the updated ROI list into the sweep
        pipeline's mask cache."""
        self._on_rois_changed = callback

    def set_image_shape(self, image_shape: tuple[int, int]) -> None:
        self._image_shape = image_shape

    def show_frame(self, image) -> None:
        if self._image_shape is None:
            self._image_shape = image.shape
        self.image_view.show_frame(image)

    def rois(self) -> list[AreaRoi]:
        return list(self._rois.values())

    def add_roi(self, center_x: float, center_y: float) -> AreaRoi:
        roi_id = next_area_roi_id(self._rois.keys())
        roi = AreaRoi(
            area_roi_id=roi_id,
            center_x=center_x,
            center_y=center_y,
            sample_radius_px=_DEFAULT_SAMPLE_RADIUS_PX,
            reference_inner_diameter_px=_DEFAULT_REFERENCE_INNER_DIAMETER_PX,
            reference_outer_diameter_px=_DEFAULT_REFERENCE_OUTER_DIAMETER_PX,
        )
        self._add_roi_object(roi)
        self._notify_changed()
        return roi

    def remove_roi(self, roi_id: int) -> None:
        if roi_id not in self._rois:
            return
        self.image_view.view_box.removeItem(self._sample_items.pop(roi_id))
        inner_item, outer_item = self._reference_items.pop(roi_id)
        self.image_view.view_box.removeItem(inner_item)
        self.image_view.view_box.removeItem(outer_item)
        del self._rois[roi_id]
        self._refresh_list()
        self._notify_changed()

    def load_rois(self, rois: list[AreaRoi]) -> None:
        """Replace every current ROI with the given list - e.g. after a
        session restore (storage/hdf5_export.py's read_imaging_session())."""
        for roi_id in list(self._rois.keys()):
            self.remove_roi(roi_id)
        for roi in rois:
            self._add_roi_object(roi)
        self._notify_changed()

    # -- internal: building/syncing overlay items --------------------------------

    def _add_roi_object(self, roi: AreaRoi) -> None:
        self._rois[roi.area_roi_id] = roi

        sample_item = pg.CircleROI(
            [roi.center_x - roi.sample_radius_px, roi.center_y - roi.sample_radius_px],
            [roi.sample_radius_px * 2, roi.sample_radius_px * 2],
            pen=_SAMPLE_PEN,
            movable=True,
            resizable=True,
            rotatable=False,
            removable=True,
        )
        sample_item.sigRegionChangeFinished.connect(lambda _item, rid=roi.area_roi_id: self._on_sample_item_changed(rid))
        sample_item.sigRemoveRequested.connect(lambda _item, rid=roi.area_roi_id: self.remove_roi(rid))
        self.image_view.view_box.addItem(sample_item)
        self._sample_items[roi.area_roi_id] = sample_item

        inner_item, outer_item = self._build_reference_items(roi)
        self.image_view.view_box.addItem(inner_item)
        self.image_view.view_box.addItem(outer_item)
        self._reference_items[roi.area_roi_id] = (inner_item, outer_item)

        self._refresh_list()

    def _build_reference_items(self, roi: AreaRoi) -> tuple[pg.CircleROI, pg.CircleROI]:
        inner_diameter = roi.reference_inner_diameter_px or 0.0
        outer_diameter = roi.reference_outer_diameter_px or 0.0
        inner_item = pg.CircleROI(
            [roi.center_x - inner_diameter / 2, roi.center_y - inner_diameter / 2],
            [inner_diameter, inner_diameter],
            pen=_REFERENCE_PEN,
            movable=False,
            resizable=False,
            rotatable=False,
        )
        outer_item = pg.CircleROI(
            [roi.center_x - outer_diameter / 2, roi.center_y - outer_diameter / 2],
            [outer_diameter, outer_diameter],
            pen=_REFERENCE_PEN,
            movable=False,
            resizable=False,
            rotatable=False,
        )
        # Purely visual, not independently editable - dragging/resizing
        # happens on the sample item only, matching v1's simpler
        # interaction model (per-ROI reference diameters are edited
        # numerically, see the side panel's spin boxes).
        for item in (inner_item, outer_item):
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        return inner_item, outer_item

    def _on_sample_item_changed(self, roi_id: int) -> None:
        roi = self._rois.get(roi_id)
        item = self._sample_items.get(roi_id)
        if roi is None or item is None:
            return
        size = item.size()
        radius = max(float(size[0]), float(size[1])) / 2.0
        pos = item.pos()
        center_x = float(pos[0]) + radius
        center_y = float(pos[1]) + radius
        roi.sample_radius_px = radius
        move_roi(roi, center_x=center_x, center_y=center_y, image_shape=self._image_shape)
        # Re-sync the item to the (possibly clamped) center/radius actually
        # applied, so a drag past the image edge snaps back visually too.
        item.setPos([roi.center_x - radius, roi.center_y - radius], finish=False)
        item.setSize([radius * 2, radius * 2], finish=False)
        self._reposition_reference_items(roi_id)
        self._refresh_list()
        self._notify_changed()

    def _reposition_reference_items(self, roi_id: int) -> None:
        roi = self._rois.get(roi_id)
        items = self._reference_items.get(roi_id)
        if roi is None or items is None:
            return
        inner_item, outer_item = items
        inner_diameter = roi.reference_inner_diameter_px or 0.0
        outer_diameter = roi.reference_outer_diameter_px or 0.0
        inner_item.setPos([roi.center_x - inner_diameter / 2, roi.center_y - inner_diameter / 2], finish=False)
        inner_item.setSize([inner_diameter, inner_diameter], finish=False)
        outer_item.setPos([roi.center_x - outer_diameter / 2, roi.center_y - outer_diameter / 2], finish=False)
        outer_item.setSize([outer_diameter, outer_diameter], finish=False)

    # -- side panel wiring --------------------------------------------------------

    def _refresh_list(self) -> None:
        selected_id = self._selected_roi_id()
        self._roi_list.blockSignals(True)
        self._roi_list.clear()
        for roi in sorted(self._rois.values(), key=lambda r: r.area_roi_id):
            label = f"ROI {roi.area_roi_id}  ({roi.center_x:.0f}, {roi.center_y:.0f})  r={roi.sample_radius_px:.1f}px"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, roi.area_roi_id)
            self._roi_list.addItem(item)
            if roi.area_roi_id == selected_id:
                self._roi_list.setCurrentItem(item)
        self._roi_list.blockSignals(False)

    def _selected_roi_id(self) -> int | None:
        item = self._roi_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            self._set_reference_controls_enabled(False)
            return
        roi_id = current.data(Qt.ItemDataRole.UserRole)
        roi = self._rois.get(roi_id)
        if roi is None:
            self._set_reference_controls_enabled(False)
            return
        self._set_reference_controls_enabled(True)
        self._reference_inner_spin.blockSignals(True)
        self._reference_outer_spin.blockSignals(True)
        self._reference_inner_spin.setValue(roi.reference_inner_diameter_px or 0.0)
        self._reference_outer_spin.setValue(roi.reference_outer_diameter_px or 0.0)
        self._reference_inner_spin.blockSignals(False)
        self._reference_outer_spin.blockSignals(False)

    def _set_reference_controls_enabled(self, enabled: bool) -> None:
        self._reference_inner_spin.setEnabled(enabled)
        self._reference_outer_spin.setEnabled(enabled)

    def _on_add_clicked(self) -> None:
        if self._image_shape is not None:
            height, width = self._image_shape
            self.add_roi(width / 2.0, height / 2.0)
        else:
            self.add_roi(0.0, 0.0)

    def _on_delete_clicked(self) -> None:
        roi_id = self._selected_roi_id()
        if roi_id is not None:
            self.remove_roi(roi_id)

    def _on_reference_diameters_edited(self, _value: float) -> None:
        roi_id = self._selected_roi_id()
        if roi_id is None:
            return
        roi = self._rois.get(roi_id)
        if roi is None:
            return
        inner = self._reference_inner_spin.value()
        outer = self._reference_outer_spin.value()
        if outer < inner:
            outer = inner
            self._reference_outer_spin.blockSignals(True)
            self._reference_outer_spin.setValue(outer)
            self._reference_outer_spin.blockSignals(False)
        roi.reference_inner_diameter_px = inner
        roi.reference_outer_diameter_px = outer
        self._reposition_reference_items(roi_id)
        self._notify_changed()

    def _notify_changed(self) -> None:
        if self._on_rois_changed is not None:
            self._on_rois_changed()
