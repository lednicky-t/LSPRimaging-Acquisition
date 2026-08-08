from __future__ import annotations

import unittest

import numpy as np

from lspri_acq_app.domain.extinction import (
    absorbance_from_means,
    build_absorbance_spectrum_result,
    centroid_wavelength,
    peak_absorbance,
)


class AbsorbanceFromMeansTests(unittest.TestCase):
    def test_equal_means_give_zero_absorbance(self) -> None:
        result = absorbance_from_means(np.array([10.0, 20.0]), np.array([10.0, 20.0]))
        np.testing.assert_allclose(result, [0.0, 0.0])

    def test_sample_below_reference_gives_positive_absorbance(self) -> None:
        result = absorbance_from_means(np.array([1.0]), np.array([10.0]))
        self.assertGreater(result[0], 0.0)

    def test_nonpositive_values_are_nan_not_raising(self) -> None:
        result = absorbance_from_means(np.array([0.0, -5.0, 10.0]), np.array([10.0, 10.0, 10.0]))
        self.assertTrue(np.isnan(result[0]))
        self.assertTrue(np.isnan(result[1]))
        self.assertFalse(np.isnan(result[2]))

    def test_mismatched_shapes_raise(self) -> None:
        with self.assertRaises(ValueError):
            absorbance_from_means(np.array([1.0, 2.0]), np.array([1.0]))


class BuildAbsorbanceSpectrumResultTests(unittest.TestCase):
    def test_carries_roi_and_cube_identity_through(self) -> None:
        result = build_absorbance_spectrum_result(
            roi_id=3,
            cube_index=7,
            wavelengths_nm=np.array([450.0, 500.0]),
            sample_means=np.array([5.0, 5.0]),
            reference_means=np.array([10.0, 10.0]),
        )
        self.assertEqual(result.roi_id, 3)
        self.assertEqual(result.cube_index, 7)
        np.testing.assert_allclose(result.wavelengths_nm, [450.0, 500.0])


class PeakAbsorbanceTests(unittest.TestCase):
    def test_returns_the_highest_finite_point(self) -> None:
        wavelengths = np.array([450.0, 500.0, 550.0])
        absorbance = np.array([0.1, 0.9, 0.3])
        self.assertEqual(peak_absorbance(wavelengths, absorbance), (500.0, 0.9))

    def test_ignores_nan_points(self) -> None:
        wavelengths = np.array([450.0, 500.0, 550.0])
        absorbance = np.array([np.nan, 0.2, 0.1])
        self.assertEqual(peak_absorbance(wavelengths, absorbance), (500.0, 0.2))

    def test_all_nan_returns_none(self) -> None:
        wavelengths = np.array([450.0, 500.0])
        absorbance = np.array([np.nan, np.nan])
        self.assertIsNone(peak_absorbance(wavelengths, absorbance))


class CentroidWavelengthTests(unittest.TestCase):
    def test_symmetric_peak_centers_on_its_own_wavelength(self) -> None:
        wavelengths = np.array([490.0, 500.0, 510.0])
        absorbance = np.array([0.1, 0.9, 0.1])
        centroid = centroid_wavelength(wavelengths, absorbance)
        self.assertAlmostEqual(centroid, 500.0, places=6)

    def test_flat_curve_has_no_centroid(self) -> None:
        wavelengths = np.array([490.0, 500.0, 510.0])
        absorbance = np.array([0.5, 0.5, 0.5])
        self.assertIsNone(centroid_wavelength(wavelengths, absorbance))

    def test_too_few_finite_points_returns_none(self) -> None:
        wavelengths = np.array([490.0, 500.0])
        absorbance = np.array([0.5, np.nan])
        self.assertIsNone(centroid_wavelength(wavelengths, absorbance))


if __name__ == "__main__":
    unittest.main()
