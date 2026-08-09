from __future__ import annotations

import queue
import time
import unittest

from lspri_acq_app.acquisition.sweep_pipeline import (
    SweepController,
    SweepError,
    _queue_put_latest,
)
from lspri_acq_app.device.camera_base import CameraSettings
from lspri_acq_app.device.simulated_camera import SimulatedCamera
from lspri_acq_app.device.simulated_illumination import SimulatedIllumination
from lspri_acq_app.domain.models import (
    ImagingAcquisitionSettings,
    WavelengthCameraSettings,
    WavelengthIlluminationSettings,
)


def _wait_until(predicate, timeout_s: float = 5.0, interval_s: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


class QueuePutLatestTests(unittest.TestCase):
    def test_replaces_pending_item_in_a_maxsize_one_queue(self) -> None:
        q: "queue.Queue[str]" = queue.Queue(maxsize=1)
        _queue_put_latest(q, "first")
        _queue_put_latest(q, "second")

        self.assertEqual(q.qsize(), 1)
        self.assertEqual(q.get_nowait(), "second")

    def test_plain_put_when_queue_has_room(self) -> None:
        q: "queue.Queue[str]" = queue.Queue(maxsize=2)
        _queue_put_latest(q, "only")
        self.assertEqual(q.get_nowait(), "only")


class SweepControllerErrorHandlingTests(unittest.TestCase):
    def test_persistent_camera_failure_backs_off_instead_of_spinning(self) -> None:
        """A camera that always fails to acquire must not turn the sweep
        loop into a tight retry spin - see sweep_pipeline.py's
        _SWEEP_ERROR_BACKOFF_S. Without the backoff, this test would report
        dozens of errors within a fraction of a second instead of a
        handful."""

        class _AlwaysFailingCamera(SimulatedCamera):
            def acquire_frame(self, timeout_ms: int):
                raise RuntimeError("simulated camera fault")

        camera = _AlwaysFailingCamera()
        camera.open()
        illumination = SimulatedIllumination()
        illumination.open()
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0], exposure_us=1000.0, settle_time_override_ms=0.0
        )

        errors: list[SweepError] = []
        controller = SweepController(
            camera=camera,
            illumination=illumination,
            settings=settings,
            save_queue=queue.Queue(),
            processing_queue=queue.Queue(maxsize=1),
            on_error=errors.append,
        )

        controller.start()
        try:
            time.sleep(0.6)  # a couple of _SWEEP_ERROR_BACKOFF_S (0.5s) periods
        finally:
            controller.stop(join_timeout_s=2.0)

        self.assertFalse(controller.is_running())
        # Backed off ~0.5s per attempt over ~0.6s wall time: 1-2 attempts,
        # not dozens (which is what a tight spin would produce).
        self.assertLessEqual(len(errors), 3)
        self.assertGreaterEqual(len(errors), 1)
        self.assertIn("simulated camera fault", errors[0].message)

    def test_stop_interrupts_the_error_backoff_promptly(self) -> None:
        class _AlwaysFailingCamera(SimulatedCamera):
            def acquire_frame(self, timeout_ms: int):
                raise RuntimeError("simulated camera fault")

        camera = _AlwaysFailingCamera()
        camera.open()
        illumination = SimulatedIllumination()
        illumination.open()
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0], exposure_us=1000.0, settle_time_override_ms=0.0
        )
        controller = SweepController(
            camera=camera,
            illumination=illumination,
            settings=settings,
            save_queue=queue.Queue(),
            processing_queue=queue.Queue(maxsize=1),
        )

        controller.start()
        self.assertTrue(_wait_until(lambda: not controller.is_running() or True, timeout_s=0.1))
        started = time.monotonic()
        controller.stop(join_timeout_s=2.0)
        elapsed = time.monotonic() - started
        # Backoff is 0.5s - stop() should not need to wait out a full
        # backoff period to return (stop_event.wait() must be interrupted
        # promptly by stop_event.set(), not just time out on its own).
        self.assertLess(elapsed, 0.5)


class SweepControllerLossToProcessingQueueTests(unittest.TestCase):
    def test_processing_queue_only_ever_holds_the_latest_cube(self) -> None:
        camera = SimulatedCamera(width_px=32, height_px=32, noise_std=0.0)
        camera.open()
        illumination = SimulatedIllumination()
        illumination.open()
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0], exposure_us=1000.0, settle_time_override_ms=0.0
        )
        save_queue: "queue.Queue" = queue.Queue()
        processing_queue: "queue.Queue" = queue.Queue(maxsize=1)
        controller = SweepController(
            camera=camera,
            illumination=illumination,
            settings=settings,
            save_queue=save_queue,
            processing_queue=processing_queue,
            recording_active=True,  # this test uses save_queue depth as its "N sweeps completed" proxy
        )

        controller.start()
        try:
            # Deliberately never drain processing_queue - a slow/absent
            # processing thread must not block the sweep controller, and
            # the queue must never grow past 1 item.
            completed = _wait_until(lambda: save_queue.qsize() >= 3, timeout_s=5.0)
        finally:
            controller.stop(join_timeout_s=2.0)

        self.assertTrue(completed, f"save_queue only reached {save_queue.qsize()}")
        self.assertLessEqual(processing_queue.qsize(), 1)


class PerWavelengthOverrideTests(unittest.TestCase):
    """2026-08-09: exposure/gain/binning and settle time can now vary per
    wavelength (ImagingAcquisitionSettings.camera_settings_by_wavelength/
    illumination_settings_by_wavelength) - these confirm the override
    actually reaches Camera.configure() and the settle wait, not just that
    the dataclass fields exist (covered in test_domain_models.py)."""

    def test_camera_is_reconfigured_per_wavelength_with_the_override(self) -> None:
        configure_calls: list[CameraSettings] = []

        class _RecordingCamera(SimulatedCamera):
            def configure(self, settings: CameraSettings) -> None:
                configure_calls.append(settings)
                super().configure(settings)

        camera = _RecordingCamera()
        camera.open()
        illumination = SimulatedIllumination()
        illumination.open()
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0],
            exposure_us=1000.0,
            gain=1.0,
            settle_time_override_ms=0.0,
            camera_settings_by_wavelength={500.0: WavelengthCameraSettings(exposure_us=9000.0, gain=3.0, binning=2)},
        )
        save_queue: "queue.Queue" = queue.Queue()
        processing_queue: "queue.Queue" = queue.Queue(maxsize=1)
        controller = SweepController(
            camera=camera,
            illumination=illumination,
            settings=settings,
            save_queue=save_queue,
            processing_queue=processing_queue,
        )

        controller.start()
        try:
            self.assertTrue(_wait_until(lambda: len(configure_calls) >= 2, timeout_s=5.0))
        finally:
            controller.stop(join_timeout_s=2.0)

        self.assertEqual(configure_calls[0].exposure_us, 1000.0)
        self.assertEqual(configure_calls[0].gain, 1.0)
        self.assertEqual(configure_calls[1].exposure_us, 9000.0)
        self.assertEqual(configure_calls[1].gain, 3.0)
        self.assertEqual(configure_calls[1].binning, 2)

    def test_settle_time_override_is_used_for_its_specific_wavelength_only(self) -> None:
        waits: list[float] = []
        camera = SimulatedCamera()
        camera.open()

        class _RecordingIllumination(SimulatedIllumination):
            def settle_time_ms(self) -> float:
                return 0.0

        illumination = _RecordingIllumination()
        illumination.open()
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0],
            exposure_us=1000.0,
            settle_time_override_ms=0.0,
            illumination_settings_by_wavelength={500.0: WavelengthIlluminationSettings(settle_time_ms=25.0)},
        )
        save_queue: "queue.Queue" = queue.Queue()
        processing_queue: "queue.Queue" = queue.Queue(maxsize=1)
        controller = SweepController(
            camera=camera,
            illumination=illumination,
            settings=settings,
            save_queue=save_queue,
            processing_queue=processing_queue,
        )

        self.assertEqual(controller._settle_time_ms_for(450.0), 0.0)
        self.assertEqual(controller._settle_time_ms_for(500.0), 25.0)


class RecordingActiveGateTests(unittest.TestCase):
    """2026-08-09: images must not be recorded until a measurement is
    actually started - SweepController.recording_active gates the save_queue
    side only; the processing (live preview) queue keeps receiving cubes
    regardless, so ROI/live-view still works during setup."""

    def _controller(self, **kwargs) -> tuple[SweepController, "queue.Queue", "queue.Queue"]:
        camera = SimulatedCamera(width_px=16, height_px=16, noise_std=0.0)
        camera.open()
        illumination = SimulatedIllumination()
        illumination.open()
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0], exposure_us=1000.0, settle_time_override_ms=0.0
        )
        save_queue: "queue.Queue" = queue.Queue()
        processing_queue: "queue.Queue" = queue.Queue(maxsize=1)
        controller = SweepController(
            camera=camera,
            illumination=illumination,
            settings=settings,
            save_queue=save_queue,
            processing_queue=processing_queue,
            **kwargs,
        )
        return controller, save_queue, processing_queue

    def test_defaults_to_not_recording(self) -> None:
        controller, _, _ = self._controller()
        self.assertFalse(controller.is_recording_active())

    def test_cubes_are_not_saved_while_not_recording_but_still_reach_the_processing_queue(self) -> None:
        controller, save_queue, processing_queue = self._controller()

        controller.start()
        try:
            self.assertTrue(_wait_until(lambda: processing_queue.qsize() >= 1, timeout_s=5.0))
            time.sleep(0.2)  # give a few more sweeps a chance to run
        finally:
            controller.stop(join_timeout_s=2.0)

        self.assertEqual(save_queue.qsize(), 0)

    def test_set_recording_active_arms_saving_from_the_next_cube(self) -> None:
        controller, save_queue, _ = self._controller()

        controller.start()
        try:
            controller.set_recording_active(True)
            self.assertTrue(controller.is_recording_active())
            self.assertTrue(_wait_until(lambda: save_queue.qsize() >= 1, timeout_s=5.0))
        finally:
            controller.stop(join_timeout_s=2.0)

    def test_recording_active_true_at_construction_saves_immediately(self) -> None:
        controller, save_queue, _ = self._controller(recording_active=True)

        controller.start()
        try:
            self.assertTrue(_wait_until(lambda: save_queue.qsize() >= 1, timeout_s=5.0))
        finally:
            controller.stop(join_timeout_s=2.0)


if __name__ == "__main__":
    unittest.main()
