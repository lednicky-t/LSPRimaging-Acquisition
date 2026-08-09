"""Qt widget tests for gui/roi_panel.py. Real widgets, real pyqtgraph
CircleROI items - no mocking of Qt/pyqtgraph itself. Drag/resize gestures
are exercised by moving the underlying CircleROI item directly (setPos/
setSize) and invoking the same change handler a real drag would trigger via
sigRegionChangeFinished, rather than simulating raw mouse events - this
tests the same sync logic a real drag exercises without needing a full
input-event harness.
"""

from __future__ import annotations

import unittest

import numpy as np
from PyQt6.QtWidgets import QApplication

from lspri_acq_app.domain.roi import AreaRoi
from lspri_acq_app.gui.roi_panel import RoiPanel

_APP = QApplication.instance() or QApplication([])


def _close_and_flush(widget) -> None:
    """Explicit widget teardown, not left to Python GC's own timing.

    RoiPanel holds pyqtgraph CircleROI items added to a ViewBox's scene
    graph (not plain Qt child widgets) - letting an unparented RoiPanel be
    collected whenever the cyclic GC happens to run (rather than
    deterministically, right after each test) caused a real, reproducible
    "Windows fatal exception: access violation" during garbage-collection a
    few tests later in this same file, once enough uncleaned panels had
    piled up. close() + deleteLater() + processEvents() makes Qt actually
    tear down the widget/scene-graph tree now, in a known-good order,
    instead of leaving that to whenever GC gets around to it.
    """
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()


class RoiPanelAddRemoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = RoiPanel()
        self.addCleanup(_close_and_flush, self.panel)
        self.panel.set_image_shape((200, 300))

    def test_add_roi_creates_area_roi_with_defaults(self) -> None:
        roi = self.panel.add_roi(50.0, 60.0)
        self.assertEqual(roi.area_roi_id, 1)
        self.assertEqual((roi.center_x, roi.center_y), (50.0, 60.0))
        self.assertIsNotNone(roi.reference_inner_diameter_px)
        self.assertIsNotNone(roi.reference_outer_diameter_px)
        self.assertEqual([r.area_roi_id for r in self.panel.rois()], [roi.area_roi_id])

    def test_add_roi_creates_overlay_items(self) -> None:
        roi = self.panel.add_roi(50.0, 60.0)
        self.assertIn(roi.area_roi_id, self.panel._sample_items)
        self.assertIn(roi.area_roi_id, self.panel._reference_items)
        self.assertIn(self.panel._sample_items[roi.area_roi_id], self.panel.image_view.view_box.allChildren())

    def test_second_roi_gets_next_id(self) -> None:
        first = self.panel.add_roi(10.0, 10.0)
        second = self.panel.add_roi(20.0, 20.0)
        self.assertEqual(first.area_roi_id, 1)
        self.assertEqual(second.area_roi_id, 2)

    def test_remove_roi_deletes_model_and_overlay(self) -> None:
        roi = self.panel.add_roi(50.0, 60.0)
        self.panel.remove_roi(roi.area_roi_id)
        self.assertEqual(self.panel.rois(), [])
        self.assertNotIn(roi.area_roi_id, self.panel._sample_items)
        self.assertNotIn(roi.area_roi_id, self.panel._reference_items)

    def test_remove_unknown_roi_id_is_a_no_op(self) -> None:
        self.panel.remove_roi(999)  # must not raise

    def test_rois_changed_callback_fires_on_add_and_remove(self) -> None:
        calls = []
        self.panel.set_on_rois_changed(lambda: calls.append(1))
        roi = self.panel.add_roi(10.0, 10.0)
        self.panel.remove_roi(roi.area_roi_id)
        self.assertEqual(len(calls), 2)


class RoiPanelDragSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = RoiPanel()
        self.addCleanup(_close_and_flush, self.panel)
        self.panel.set_image_shape((200, 300))
        self.roi = self.panel.add_roi(100.0, 100.0)

    def _simulate_drag(self, *, new_center_x: float, new_center_y: float, new_radius: float | None = None) -> None:
        item = self.panel._sample_items[self.roi.area_roi_id]
        radius = new_radius if new_radius is not None else self.roi.sample_radius_px
        item.setPos([new_center_x - radius, new_center_y - radius], finish=False)
        item.setSize([radius * 2, radius * 2], finish=False)
        self.panel._on_sample_item_changed(self.roi.area_roi_id)

    def test_move_updates_area_roi_center(self) -> None:
        self._simulate_drag(new_center_x=150.0, new_center_y=120.0)
        self.assertEqual((self.roi.center_x, self.roi.center_y), (150.0, 120.0))

    def test_resize_updates_sample_radius(self) -> None:
        self._simulate_drag(new_center_x=100.0, new_center_y=100.0, new_radius=25.0)
        self.assertEqual(self.roi.sample_radius_px, 25.0)

    def test_move_past_image_edge_is_clamped(self) -> None:
        self._simulate_drag(new_center_x=-500.0, new_center_y=-500.0)
        self.assertGreaterEqual(self.roi.center_x, 0.0)
        self.assertGreaterEqual(self.roi.center_y, 0.0)

    def test_move_repositions_reference_overlay(self) -> None:
        inner_item, outer_item = self.panel._reference_items[self.roi.area_roi_id]
        self._simulate_drag(new_center_x=150.0, new_center_y=120.0)
        inner_diameter = self.roi.reference_inner_diameter_px
        expected_pos = [150.0 - inner_diameter / 2, 120.0 - inner_diameter / 2]
        actual_pos = list(inner_item.pos())
        self.assertAlmostEqual(actual_pos[0], expected_pos[0], places=3)
        self.assertAlmostEqual(actual_pos[1], expected_pos[1], places=3)

    def test_drag_fires_rois_changed_callback(self) -> None:
        calls = []
        self.panel.set_on_rois_changed(lambda: calls.append(1))
        self._simulate_drag(new_center_x=120.0, new_center_y=110.0)
        self.assertEqual(len(calls), 1)


class RoiPanelReferenceEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = RoiPanel()
        self.addCleanup(_close_and_flush, self.panel)
        self.panel.set_image_shape((200, 300))
        self.roi = self.panel.add_roi(100.0, 100.0)
        # Selecting the ROI in the list is what enables the reference spinboxes.
        self.panel._roi_list.setCurrentRow(0)

    def test_editing_inner_diameter_updates_roi(self) -> None:
        self.panel._reference_inner_spin.setValue(15.0)
        self.assertEqual(self.roi.reference_inner_diameter_px, 15.0)

    def test_outer_cannot_go_below_inner(self) -> None:
        self.panel._reference_inner_spin.setValue(40.0)
        self.panel._reference_outer_spin.setValue(20.0)
        self.assertGreaterEqual(self.roi.reference_outer_diameter_px, self.roi.reference_inner_diameter_px)


class LoadRoisTests(unittest.TestCase):
    """load_rois() - 2026-08-09, the session-restore counterpart to
    add_roi(): replaces every current ROI with a given (possibly
    fully-specified, e.g. from read_imaging_session()) list."""

    def setUp(self) -> None:
        self.panel = RoiPanel()
        self.addCleanup(_close_and_flush, self.panel)
        self.panel.set_image_shape((200, 300))

    def test_replaces_existing_rois(self) -> None:
        self.panel.add_roi(10.0, 10.0)
        self.panel.load_rois(
            [AreaRoi(area_roi_id=5, center_x=50.0, center_y=60.0, sample_radius_px=8.0)]
        )
        self.assertEqual([r.area_roi_id for r in self.panel.rois()], [5])
        self.assertEqual((self.panel.rois()[0].center_x, self.panel.rois()[0].center_y), (50.0, 60.0))

    def test_load_empty_list_clears_all_rois(self) -> None:
        self.panel.add_roi(10.0, 10.0)
        self.panel.load_rois([])
        self.assertEqual(self.panel.rois(), [])

    def test_loaded_rois_get_real_overlay_items(self) -> None:
        self.panel.load_rois([AreaRoi(area_roi_id=1, center_x=20.0, center_y=30.0, sample_radius_px=5.0)])
        self.assertIn(1, self.panel._sample_items)
        self.assertIn(1, self.panel._reference_items)

    def test_notifies_on_rois_changed_callback(self) -> None:
        calls = []
        self.panel.set_on_rois_changed(lambda: calls.append(1))
        self.panel.load_rois([AreaRoi(area_roi_id=1, center_x=20.0, center_y=30.0, sample_radius_px=5.0)])
        self.assertGreaterEqual(len(calls), 1)


class ImageViewPanelTests(unittest.TestCase):
    def test_show_frame_does_not_raise(self) -> None:
        panel = RoiPanel()
        self.addCleanup(_close_and_flush, panel)
        image = np.zeros((100, 120), dtype=np.uint16)
        panel.show_frame(image)  # must not raise


if __name__ == "__main__":
    unittest.main()
