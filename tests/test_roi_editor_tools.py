from __future__ import annotations

import unittest

from lspri_acq_app.domain.roi import AreaRoi
from lspri_acq_app.domain.roi_editor_tools import (
    clamp_center_to_image,
    move_roi,
    next_area_roi_id,
    roi_outer_radius_px,
)


class RoiOuterRadiusTests(unittest.TestCase):
    def test_uses_sample_radius_when_no_reference_annulus(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=8.0)
        self.assertEqual(roi_outer_radius_px(roi), 8.0)

    def test_uses_reference_outer_radius_when_larger(self) -> None:
        roi = AreaRoi(
            area_roi_id=1,
            center_x=50.0,
            center_y=50.0,
            sample_radius_px=8.0,
            reference_inner_diameter_px=20.0,
            reference_outer_diameter_px=30.0,
        )
        self.assertEqual(roi_outer_radius_px(roi), 15.0)


class ClampCenterToImageTests(unittest.TestCase):
    def test_no_clamp_needed_inside_bounds(self) -> None:
        self.assertEqual(clamp_center_to_image(50.0, 50.0, 8.0, (100, 100)), (50.0, 50.0))

    def test_clamps_near_top_left_edge(self) -> None:
        x, y = clamp_center_to_image(-10.0, -10.0, 8.0, (100, 100))
        self.assertEqual((x, y), (8.0, 8.0))

    def test_clamps_near_bottom_right_edge(self) -> None:
        x, y = clamp_center_to_image(500.0, 500.0, 8.0, (100, 100))
        self.assertEqual((x, y), (92.0, 92.0))

    def test_no_image_means_no_clamping(self) -> None:
        self.assertEqual(clamp_center_to_image(-500.0, 9000.0, 8.0, None), (-500.0, 9000.0))


class MoveRoiTests(unittest.TestCase):
    def test_moves_and_clamps_using_outer_radius(self) -> None:
        roi = AreaRoi(
            area_roi_id=1,
            center_x=50.0,
            center_y=50.0,
            sample_radius_px=8.0,
            reference_inner_diameter_px=20.0,
            reference_outer_diameter_px=30.0,
        )
        move_roi(roi, center_x=-100.0, center_y=-100.0, image_shape=(100, 100))
        # Clamped by the 15px outer (reference) radius, not the 8px sample radius.
        self.assertEqual((roi.center_x, roi.center_y), (15.0, 15.0))

    def test_mutates_in_place(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=8.0)
        result = move_roi(roi, center_x=60.0, center_y=70.0)
        self.assertIsNone(result)
        self.assertEqual((roi.center_x, roi.center_y), (60.0, 70.0))


class NextAreaRoiIdTests(unittest.TestCase):
    def test_starts_at_one_when_empty(self) -> None:
        self.assertEqual(next_area_roi_id([]), 1)

    def test_fills_a_gap_before_extending(self) -> None:
        self.assertEqual(next_area_roi_id([1, 3]), 2)

    def test_extends_past_a_contiguous_run(self) -> None:
        self.assertEqual(next_area_roi_id([1, 2, 3]), 4)


if __name__ == "__main__":
    unittest.main()
