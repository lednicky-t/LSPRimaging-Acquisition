"""Basler camera driver (pypylon / GenICam).

Software-triggered single-frame acquisition, per the architecture plan's v1
scope (illumination.set_wavelength() -> sleep(settle) -> camera.acquire_frame(),
no HW/TTL trigger yet - that's an explicit later fast-follow, not v1).

Pixel-format/binning/exposure handling below mirrors
spikes/lspri_acq_phase0/benchmark_ui.py's PylonBackend, the one piece of this
code that IS real-hardware-verified (Phase 0, three Basler-family cameras).
The single-shot GrabOne-vs-streaming call pattern and the software-trigger
node sequence (TriggerSelector/TriggerMode/TriggerSource/ExecuteSoftwareTrigger)
are new here - that spike only ever free-ran continuous capture, never
grabbed one frame on command the way a real wavelength sweep needs to.

**Not yet verified against real hardware**: no Basler camera was attached in
the environment this was written in (confirmed via
pylon.TlFactory.EnumerateDevices() returning 0 devices at the time - see the
2026-08-08 build-log entry). The pixel-format/binning/exposure node calls
follow the exact pattern already proven on real hardware in Phase 0; the
open()-by-serial-number and software-trigger sequence are standard GenICam
patterns but have not been exercised against a physical camera yet. Verify
end-to-end (open, configure, acquire_frame, close) against real hardware
before relying on this for a real experiment.
"""

from __future__ import annotations

from datetime import datetime, timezone

from lspri_acq_app.device.camera_base import Camera, CameraCapabilities, CameraError, CameraSettings
from lspri_acq_app.domain.models import Frame


def discover_basler_cameras() -> list[object]:
    """Enumerate attached Basler devices (pylon.DeviceInfo objects).

    A real, hardware-verifiable call regardless of whether a camera is
    attached - returns an empty list rather than raising when none is found,
    the same "discovery found nothing" contract every other device family's
    discover_and_connect callback expects.
    """
    from pypylon import pylon

    return list(pylon.TlFactory.GetInstance().EnumerateDevices())


class BaslerCamera(Camera):
    """Talks to one Basler camera, selected by serial number.

    Exposes ._claim_owner/.port/.is_connected() beyond the Camera ABC's own
    surface - the shape lspr_acq_shell.DeviceCommunicationService's driver
    connect-factory registry needs (see device/registry.py), the same extra
    surface RegloICCClient/AMFSwitchController already implement for the
    same reason (they're not just drivers, they're also the "connection
    object" DeviceCommunicationService tracks per label).
    """

    def __init__(self, serial_number: str) -> None:
        self._serial_number = serial_number
        self._claim_owner = f"basler:{id(self)}"
        self.port = serial_number
        self._camera = None

    def open(self) -> None:
        from pypylon import pylon

        tl_factory = pylon.TlFactory.GetInstance()
        device_info = pylon.DeviceInfo()
        device_info.SetSerialNumber(self._serial_number)
        try:
            device = tl_factory.CreateDevice(device_info)
        except Exception as exc:
            raise CameraError(
                f"No Basler camera with serial number {self._serial_number!r} found: {exc}"
            ) from exc
        self._camera = pylon.InstantCamera(device)
        self._camera.Open()
        self._configure_software_trigger()

    def _configure_software_trigger(self) -> None:
        camera = self._camera
        camera.TriggerSelector.SetValue("FrameStart")
        camera.TriggerMode.SetValue("On")
        camera.TriggerSource.SetValue("Software")

    def close(self) -> None:
        if self._camera is not None and self._camera.IsOpen():
            if self._camera.IsGrabbing():
                self._camera.StopGrabbing()
            self._camera.Close()
        self._camera = None

    def is_connected(self) -> bool:
        return self._camera is not None and self._camera.IsOpen()

    def configure(self, settings: CameraSettings) -> None:
        if self._camera is None:
            raise CameraError("BaslerCamera.configure() called before open()")
        camera = self._camera

        try:
            supported_formats = list(camera.PixelFormat.Symbolics)
        except Exception:
            supported_formats = []
        if supported_formats and settings.pixel_format not in supported_formats:
            raise CameraError(
                f"{self.device_name()} does not support pixel format {settings.pixel_format!r} "
                f"(supports: {', '.join(supported_formats)})."
            )
        camera.PixelFormat.SetValue(settings.pixel_format)

        # Binning reduces resolution while keeping the full field of view -
        # some camera models don't expose it at all, so this degrades to
        # 1x1 rather than failing the whole configure() over it, matching
        # the Phase 0 spike's PylonBackend.apply_binning().
        if settings.binning != 1:
            try:
                camera.BinningHorizontal.SetValue(settings.binning)
                camera.BinningVertical.SetValue(settings.binning)
                try:
                    camera.BinningHorizontalMode.SetValue("Average")
                    camera.BinningVerticalMode.SetValue("Average")
                except Exception:
                    pass
            except Exception as exc:
                raise CameraError(
                    f"{self.device_name()} does not support {settings.binning}x binning: {exc}"
                ) from exc
        camera.Width.SetValue(camera.Width.Max)
        camera.Height.SetValue(camera.Height.Max)

        lo, hi = camera.ExposureTime.Min, camera.ExposureTime.Max
        exposure_us = min(max(settings.exposure_us, lo), hi)
        camera.ExposureTime.SetValue(exposure_us)

        if settings.gain is not None:
            camera.Gain.SetValue(settings.gain)

    def acquire_frame(self, timeout_ms: int) -> Frame:
        if self._camera is None:
            raise CameraError("BaslerCamera.acquire_frame() called before open()")
        from pypylon import pylon

        camera = self._camera
        camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
        try:
            camera.ExecuteSoftwareTrigger()
            result = camera.RetrieveResult(timeout_ms, pylon.TimeoutHandling_ThrowException)
            try:
                if not result.GrabSucceeded():
                    raise CameraError(f"Grab failed: {result.ErrorCode} {result.ErrorDescription}")
                image = result.Array.copy()
            finally:
                result.Release()
        finally:
            camera.StopGrabbing()

        return Frame(
            image=image,
            wavelength_nm=float("nan"),
            acquired_at=datetime.now(timezone.utc),
            metadata={
                "device": self.device_name(),
                "exposure_us": float(camera.ExposureTime.Value),
                "gain": self._read_gain(),
                "pixel_format": camera.PixelFormat.Value,
                "binning": self._read_binning(),
                "mode": "hardware",
            },
        )

    def _read_gain(self) -> float | None:
        try:
            return float(self._camera.Gain.Value)
        except Exception:
            return None

    def _read_binning(self) -> int:
        try:
            return int(self._camera.BinningHorizontal.Value)
        except Exception:
            return 1

    def device_name(self) -> str:
        if self._camera is not None:
            try:
                return str(self._camera.DeviceModelName.Value)
            except Exception:
                pass
        return f"Basler Camera ({self._serial_number})"

    def capabilities(self) -> CameraCapabilities:
        if self._camera is None:
            raise CameraError("BaslerCamera.capabilities() called before open()")
        camera = self._camera
        try:
            max_fps = float(camera.ResultingFrameRate.Value)
        except Exception:
            max_fps = None
        return CameraCapabilities(
            sensor_width_px=int(camera.Width.Max),
            sensor_height_px=int(camera.Height.Max),
            max_fps=max_fps,
            trigger_modes=("software",),
        )
