from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from lspri_acq_app.domain.models import (
    AbsorbanceSpectrumResult,
    Frame,
    ImagingAcquisitionSettings,
    SpectralCube,
)
from lspri_acq_app.domain.roi import AreaRoi, AreaRoiGroup


class FrameTests(unittest.TestCase):
    def test_metadata_defaults_to_empty_dict_per_instance(self) -> None:
        first = Frame(image=np.zeros((2, 2)), wavelength_nm=500.0, acquired_at=datetime.now(timezone.utc))
        second = Frame(image=np.zeros((2, 2)), wavelength_nm=500.0, acquired_at=datetime.now(timezone.utc))
        first.metadata["tag"] = "first-only"
        self.assertEqual(second.metadata, {})


class SpectralCubeTests(unittest.TestCase):
    def test_holds_frames_in_sweep_order(self) -> None:
        now = datetime.now(timezone.utc)
        frames = [
            Frame(image=np.zeros((2, 2)), wavelength_nm=nm, acquired_at=now)
            for nm in (450.0, 500.0, 550.0)
        ]
        cube = SpectralCube(frames=frames, cube_index=3, started_at=now, completed_at=now)
        self.assertEqual([frame.wavelength_nm for frame in cube.frames], [450.0, 500.0, 550.0])
        self.assertEqual(cube.cube_index, 3)


class ImagingAcquisitionSettingsTests(unittest.TestCase):
    def test_settle_time_override_defaults_to_none(self) -> None:
        settings = ImagingAcquisitionSettings(wavelengths_nm=[450.0, 500.0], exposure_us=1000.0)
        self.assertIsNone(settings.settle_time_override_ms)
        self.assertIsNone(settings.gain)


class AbsorbanceSpectrumResultTests(unittest.TestCase):
    def test_carries_roi_and_cube_identity(self) -> None:
        result = AbsorbanceSpectrumResult(
            roi_id=7,
            wavelengths_nm=np.array([450.0, 500.0]),
            absorbance=np.array([0.1, 0.2]),
            cube_index=2,
        )
        self.assertEqual(result.roi_id, 7)
        self.assertEqual(result.cube_index, 2)
        np.testing.assert_array_equal(result.wavelengths_nm, [450.0, 500.0])


class AreaRoiTests(unittest.TestCase):
    def test_defaults_match_manual_placement_use(self) -> None:
        roi = AreaRoi(area_roi_id=1, center_x=10.0, center_y=20.0, sample_radius_px=5.0)
        self.assertFalse(roi.inferred)
        self.assertEqual(roi.score, 0.0)

    def test_group_tracks_member_roi_ids(self) -> None:
        group = AreaRoiGroup(group_id="g1", name="Sample vs reference")
        group.area_roi_ids.extend([1, 2])
        self.assertEqual(group.area_roi_ids, [1, 2])


if __name__ == "__main__":
    unittest.main()
