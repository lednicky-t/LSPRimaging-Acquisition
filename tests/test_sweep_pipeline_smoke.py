"""Golden-path smoke test (architecture plan section 11/12): one complete
sweep -> cube -> extinction -> sensorgram point, with simulated devices and
2-3 ROI pairs - no hardware, no Qt. Exercises save + display/processing
concurrently, which is exactly the kind of test that would have caught a
threading bug like the Lori control SW one this project's pipeline design
was built to avoid (see sweep_pipeline.py's module docstring).
"""

from __future__ import annotations

import threading
import time
import unittest

from lspri_acq_app.acquisition.sweep_pipeline import build_sweep_pipeline
from lspri_acq_app.device.simulated_camera import SimulatedCamera
from lspri_acq_app.device.simulated_illumination import SimulatedIllumination
from lspri_acq_app.domain.models import ImagingAcquisitionSettings
from lspri_acq_app.domain.roi import AreaRoi
from lspri_acq_app.processing.cube_processing import process_cube_for_rois
from lspri_acq_app.processing.roi_extraction import RoiMaskCache


def _wait_until(predicate, timeout_s: float = 5.0, interval_s: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


class SweepPipelineSmokeTest(unittest.TestCase):
    def test_sweep_produces_saved_cubes_and_sensorgram_points(self) -> None:
        camera = SimulatedCamera(
            width_px=200,
            height_px=200,
            spot_centers_px=((60.0, 100.0), (140.0, 100.0)),
            noise_std=0.0,
        )
        camera.open()
        illumination = SimulatedIllumination(wavelength_range_nm=(400.0, 700.0))
        illumination.open()

        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0, 550.0],
            exposure_us=5000.0,
            settle_time_override_ms=0.0,  # keep the smoke test fast
        )

        rois = [
            AreaRoi(
                area_roi_id=1,
                center_x=60.0,
                center_y=100.0,
                sample_radius_px=8.0,
                reference_inner_diameter_px=20.0,
                reference_outer_diameter_px=30.0,
            ),
            AreaRoi(
                area_roi_id=2,
                center_x=140.0,
                center_y=100.0,
                sample_radius_px=8.0,
                reference_inner_diameter_px=20.0,
                reference_outer_diameter_px=30.0,
            ),
        ]
        mask_cache = RoiMaskCache()

        saved_cubes: list[int] = []
        sensorgram_points: list[tuple[int, object, float | None]] = []
        errors: list[str] = []
        lock = threading.Lock()

        class _RecordingWriter:
            def write_cube(self, cube) -> int:
                with lock:
                    saved_cubes.append(cube.cube_index)
                return sum(frame.image.nbytes for frame in cube.frames)

            def close(self) -> None:
                pass

        def process_cube(cube):
            def on_result(roi_id, completed_at, result, metric_value):
                with lock:
                    sensorgram_points.append((roi_id, completed_at, metric_value))

            process_cube_for_rois(cube, rois, mask_cache, on_result=on_result)

        pipeline = build_sweep_pipeline(
            camera=camera,
            illumination=illumination,
            settings=settings,
            writer=_RecordingWriter(),
            process_cube=process_cube,
            on_sweep_error=lambda e: errors.append(e.message),
            on_save_error=lambda e: errors.append(str(e)),
            on_processing_error=lambda e: errors.append(str(e)),
        )

        pipeline.start()
        # Sweeps start in preview-only mode (2026-08-09: images aren't saved
        # until a measurement is actually started) - this smoke test is the
        # golden "recording is on" path, so arm it explicitly.
        pipeline.set_recording_active(True)
        try:
            completed = _wait_until(lambda: len(saved_cubes) >= 2 and len(sensorgram_points) >= 2, timeout_s=5.0)
        finally:
            pipeline.stop(join_timeout_s=5.0)

        self.assertEqual(errors, [])
        self.assertTrue(completed, f"saved={saved_cubes}, points={len(sensorgram_points)}")
        self.assertGreaterEqual(len(saved_cubes), 2)

        roi_ids_seen = {roi_id for roi_id, _completed_at, _metric in sensorgram_points}
        self.assertEqual(roi_ids_seen, {1, 2})
        for _roi_id, _completed_at, metric_value in sensorgram_points:
            self.assertIsNotNone(metric_value)
            self.assertTrue(metric_value == metric_value)  # not NaN


if __name__ == "__main__":
    unittest.main()
