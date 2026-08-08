from __future__ import annotations

import unittest

import numpy as np

from lspri_acq_app.domain.roi import AreaRoi
from lspri_acq_app.processing.roi_extraction import RoiMaskCache, build_roi_mask_set, extract_roi_means


class BuildRoiMaskSetTests(unittest.TestCase):
    def test_sample_mask_is_bounding_box_cropped_not_full_image(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=5.0)
        mask_set = build_roi_mask_set(roi, image_shape=(200, 200))

        y0, y1, x0, x1 = mask_set.sample_box
        # A 5px-radius disk's bounding box should be a small crop, nowhere
        # near the full 200x200 frame - this is the whole point of the fix
        # (O(ROI area), not O(image size)).
        self.assertLessEqual(y1 - y0, 13)
        self.assertLessEqual(x1 - x0, 13)
        self.assertEqual(mask_set.sample_mask.shape, (y1 - y0, x1 - x0))

    def test_no_reference_annulus_when_diameters_unset(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=5.0)
        mask_set = build_roi_mask_set(roi, image_shape=(200, 200))
        self.assertEqual(mask_set.reference_mask.size, 0)

    def test_reference_annulus_excludes_sample_disk_area(self) -> None:
        roi = AreaRoi(
            area_roi_id=1,
            center_x=50.0,
            center_y=50.0,
            sample_radius_px=5.0,
            reference_inner_diameter_px=14.0,
            reference_outer_diameter_px=20.0,
        )
        mask_set = build_roi_mask_set(roi, image_shape=(200, 200))
        self.assertGreater(mask_set.reference_mask.sum(), 0)


class ExtractRoiMeansTests(unittest.TestCase):
    def test_sample_mean_reflects_only_the_disk_region(self) -> None:
        image = np.zeros((100, 100), dtype=np.float64)
        image[45:56, 45:56] = 100.0  # bright block under the ROI
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=5.0)
        mask_set = build_roi_mask_set(roi, image.shape)

        sample_mean, reference_mean = extract_roi_means(image, mask_set)

        self.assertGreater(sample_mean, 50.0)  # dominated by the bright block
        self.assertIsNone(reference_mean)

    def test_reference_mean_is_computed_when_annulus_configured(self) -> None:
        image = np.full((100, 100), 10.0, dtype=np.float64)
        roi = AreaRoi(
            area_roi_id=1,
            center_x=50.0,
            center_y=50.0,
            sample_radius_px=5.0,
            reference_inner_diameter_px=14.0,
            reference_outer_diameter_px=20.0,
        )
        mask_set = build_roi_mask_set(roi, image.shape)

        sample_mean, reference_mean = extract_roi_means(image, mask_set)

        self.assertAlmostEqual(sample_mean, 10.0)
        self.assertAlmostEqual(reference_mean, 10.0)

    def test_sample_region_entirely_outside_image_raises(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=-100.0, center_y=-100.0, sample_radius_px=5.0)
        mask_set = build_roi_mask_set(roi, image_shape=(100, 100))
        with self.assertRaises(ValueError):
            extract_roi_means(np.zeros((100, 100)), mask_set)


class RoiMaskCacheTests(unittest.TestCase):
    def test_returns_the_same_mask_set_for_unchanged_geometry(self) -> None:
        cache = RoiMaskCache()
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=5.0)

        first = cache.get(roi, (100, 100))
        second = cache.get(roi, (100, 100))

        self.assertIs(first, second)

    def test_rebuilds_when_geometry_changes(self) -> None:
        cache = RoiMaskCache()
        roi = AreaRoi(area_roi_id=1, center_x=50.0, center_y=50.0, sample_radius_px=5.0)
        first = cache.get(roi, (100, 100))

        roi.center_x = 60.0
        second = cache.get(roi, (100, 100))

        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
