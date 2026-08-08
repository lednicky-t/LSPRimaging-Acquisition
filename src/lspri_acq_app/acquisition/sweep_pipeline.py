"""Three-thread sweep acquisition pipeline (architecture plan section 8).

Sweep controller thread: drives one wavelength sweep at a time (set
illumination wavelength -> settle -> grab a frame -> repeat), building a
SpectralCube, then hands it to two independent consumers - a dedicated
save-writer thread (lossless, drains a real queue, never drops a cube) and
a processing thread (latest-only, drops stale cubes rather than queueing
them up). This split exists specifically so a slow save or a slow
processing cycle can never make the other one - or the next sweep step -
wait: the exact failure mode the Lori control SW audit found and fixed (see
docs/architecture/general/lspri_acq_build_log.md's 2026-08-06 entry) is
"saving shares a thread with something that has to keep pace with the next
frame."

Deliberately same-process threading.Thread + queue.Queue, not
multiprocessing.Queue: unlike sLSPR acq's current live-acquisition worker
design, image-sized payloads make repeated pickling through
multiprocessing.Queue expensive (see the architecture plan's section 8 for
the measured reasoning) - don't build the more complex version
speculatively.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from lspri_acq_app.device.camera_base import Camera, CameraSettings
from lspri_acq_app.device.illumination_base import IlluminationSource
from lspri_acq_app.domain.models import Frame, ImagingAcquisitionSettings, SpectralCube

_LOGGER = logging.getLogger("lspri_acq_app.sweep_pipeline")

_SWEEP_ERROR_BACKOFF_S = 0.5


def _queue_put_latest(target_queue: "queue.Queue", payload: object) -> None:
    """Replace the queue's pending item instead of enqueueing behind it -
    the "lossy is OK for display/processing, never for raw recording" rule
    (apps/sLSPR/acq/docs/runtime_pipeline_architecture.md), applied to
    cubes instead of spectra. Mirrors sLSPR acq's
    gui/workers.py::_queue_put_latest, adapted from multiprocessing.Queue
    to queue.Queue (identical put_nowait/get_nowait/Full/Empty API shape,
    no logic change) - assumes a maxsize=1 queue, the only case where
    "replace pending" and "drop-oldest-then-retry" converge to the same
    single-item result.
    """
    while True:
        try:
            target_queue.put_nowait(payload)
            return
        except queue.Full:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                return


@dataclass(slots=True)
class SweepError:
    cube_index: int
    message: str


class SpectralCubeBuilder:
    """Accumulates Frames for one in-progress sweep."""

    def __init__(self, cube_index: int) -> None:
        self.cube_index = cube_index
        self.started_at = datetime.now(timezone.utc)
        self._frames: list[Frame] = []

    def add(self, frame: Frame) -> None:
        self._frames.append(frame)

    def finalize(self) -> SpectralCube:
        return SpectralCube(
            frames=self._frames,
            cube_index=self.cube_index,
            started_at=self.started_at,
            completed_at=datetime.now(timezone.utc),
        )


class SweepController:
    """Owns the sweep/capture thread: while running, repeatedly sweeps the
    configured wavelength list, building one SpectralCube per sweep and
    fanning it out to a lossless save queue and a latest-only processing
    queue.

    Assumes camera/illumination are already open (device connection
    lifecycle is the device registry's job, not this class's) - only calls
    Camera.configure(), never Camera.open()/IlluminationSource.open().
    """

    def __init__(
        self,
        *,
        camera: Camera,
        illumination: IlluminationSource,
        settings: ImagingAcquisitionSettings,
        save_queue: "queue.Queue[SpectralCube]",
        processing_queue: "queue.Queue[SpectralCube]",
        on_error: Callable[[SweepError], None] | None = None,
        acquire_timeout_ms: int = 5000,
    ) -> None:
        self._camera = camera
        self._illumination = illumination
        self._settings = settings
        self._save_queue = save_queue
        self._processing_queue = processing_queue
        self._on_error = on_error
        self._acquire_timeout_ms = acquire_timeout_ms
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_cube_index = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="SweepController", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_s)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            self._camera.configure(
                CameraSettings(exposure_us=self._settings.exposure_us, gain=self._settings.gain)
            )
        except Exception as exc:
            self._report_error(SweepError(self._next_cube_index, f"Camera configure failed: {exc}"))
            return

        while not self._stop_event.is_set():
            cube = self._run_one_sweep()
            if cube is None:
                # A failed sweep (see _run_one_sweep) retries from the next
                # loop iteration - back off briefly first so a persistent
                # hardware fault doesn't spin this into a tight retry loop
                # hammering the device and flooding logs. stop_event.wait()
                # rather than time.sleep() so stop() still interrupts this
                # immediately instead of waiting out the backoff.
                self._stop_event.wait(_SWEEP_ERROR_BACKOFF_S)
                continue
            self._save_queue.put(cube)  # lossless: unbounded, never drops a cube
            _queue_put_latest(self._processing_queue, cube)  # latest-only

    def _run_one_sweep(self) -> SpectralCube | None:
        cube_index = self._next_cube_index
        builder = SpectralCubeBuilder(cube_index)
        for wavelength_nm in self._settings.wavelengths_nm:
            if self._stop_event.is_set():
                return None
            try:
                self._illumination.set_wavelength(wavelength_nm)
                settle_ms = (
                    self._settings.settle_time_override_ms
                    if self._settings.settle_time_override_ms is not None
                    else self._illumination.settle_time_ms()
                )
                if settle_ms > 0:
                    self._stop_event.wait(settle_ms / 1000.0)
                frame = self._camera.acquire_frame(timeout_ms=self._acquire_timeout_ms)
                frame.wavelength_nm = wavelength_nm
                builder.add(frame)
            except Exception as exc:
                self._report_error(SweepError(cube_index, f"Step at {wavelength_nm}nm failed: {exc}"))
                return None
        self._next_cube_index += 1
        return builder.finalize()

    def _report_error(self, error: SweepError) -> None:
        _LOGGER.error("Sweep error (cube %d): %s", error.cube_index, error.message)
        if self._on_error is not None:
            self._on_error(error)


class SaveWriterThread:
    """Dedicated writer thread draining save_queue - must never block the
    sweep controller or the processing thread. write_cube is the only
    injection point; the real HDF5-backed writer (storage/image_writer.py,
    section 9/10 - not built yet) plugs in here. This class owns only the
    thread/queue lifecycle, matching lspr_acq_shell.AsyncTaggedWriter's own
    split of "generic plumbing" vs. "what one item actually does."
    """

    def __init__(
        self,
        *,
        save_queue: "queue.Queue[SpectralCube]",
        write_cube: Callable[[SpectralCube], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._save_queue = save_queue
        self._write_cube = write_cube
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="SaveWriterThread", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 10.0) -> None:
        """Signals stop, but the run loop still drains whatever is already
        queued before exiting - "stop" means "accept no new work after this
        call," not "abandon queued cubes" """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_s)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while True:
            try:
                cube = self._save_queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            try:
                self._write_cube(cube)
            except Exception as exc:
                _LOGGER.exception("Save writer failed on cube %d", cube.cube_index)
                if self._on_error is not None:
                    self._on_error(exc)
            finally:
                self._save_queue.task_done()


class ProcessingThread:
    """Latest-only processing thread: for each cube (skipping stale ones a
    slow cycle fell behind on, since the sweep controller feeds this queue
    via _queue_put_latest), runs process_cube - the caller supplies
    per-ROI extraction/metric/sensorgram logic (processing/
    cube_processing.py's process_cube_for_rois, typically), this class only
    owns the thread/queue lifecycle.
    """

    def __init__(
        self,
        *,
        processing_queue: "queue.Queue[SpectralCube]",
        process_cube: Callable[[SpectralCube], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._processing_queue = processing_queue
        self._process_cube = process_cube
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="ProcessingThread", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_s)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                cube = self._processing_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process_cube(cube)
            except Exception as exc:
                _LOGGER.exception("Processing failed on cube %d", cube.cube_index)
                if self._on_error is not None:
                    self._on_error(exc)


@dataclass(slots=True)
class SweepPipeline:
    """Convenience bundle: owns all three threads' start/stop together, for
    callers that don't need to manage them independently. Each component is
    still independently accessible (e.g. to swap process_cube at runtime).
    """

    sweep: SweepController
    save_writer: SaveWriterThread
    processing: ProcessingThread
    _save_queue: "queue.Queue" = field(repr=False)
    _processing_queue: "queue.Queue" = field(repr=False)

    def start(self) -> None:
        self.save_writer.start()
        self.processing.start()
        self.sweep.start()

    def stop(self, *, join_timeout_s: float = 5.0) -> None:
        # Stop producing first, then let save/processing drain and exit.
        self.sweep.stop(join_timeout_s=join_timeout_s)
        self.save_writer.stop(join_timeout_s=join_timeout_s)
        self.processing.stop(join_timeout_s=join_timeout_s)


def build_sweep_pipeline(
    *,
    camera: Camera,
    illumination: IlluminationSource,
    settings: ImagingAcquisitionSettings,
    write_cube: Callable[[SpectralCube], None],
    process_cube: Callable[[SpectralCube], None],
    on_sweep_error: Callable[[SweepError], None] | None = None,
    on_save_error: Callable[[Exception], None] | None = None,
    on_processing_error: Callable[[Exception], None] | None = None,
) -> SweepPipeline:
    save_queue: "queue.Queue[SpectralCube]" = queue.Queue()  # unbounded - lossless
    processing_queue: "queue.Queue[SpectralCube]" = queue.Queue(maxsize=1)  # latest-only

    sweep = SweepController(
        camera=camera,
        illumination=illumination,
        settings=settings,
        save_queue=save_queue,
        processing_queue=processing_queue,
        on_error=on_sweep_error,
    )
    save_writer = SaveWriterThread(save_queue=save_queue, write_cube=write_cube, on_error=on_save_error)
    processing = ProcessingThread(
        processing_queue=processing_queue, process_cube=process_cube, on_error=on_processing_error
    )
    return SweepPipeline(
        sweep=sweep,
        save_writer=save_writer,
        processing=processing,
        _save_queue=save_queue,
        _processing_queue=processing_queue,
    )
