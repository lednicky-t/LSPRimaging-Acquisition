"""Camera device abstraction.

v1 is software-triggered only (see the architecture plan's non-goals): a
synchronous single-frame acquire, no hardware-trigger/TTL-sync support.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from lspri_acq_app.domain.models import Frame


class CameraError(RuntimeError):
    """Raised when a camera operation fails."""


@dataclass(slots=True, frozen=True)
class CameraSettings:
    exposure_us: float
    gain: float | None = None
    pixel_format: str = "Mono12"
    binning: int = 1


@dataclass(slots=True, frozen=True)
class CameraCapabilities:
    sensor_width_px: int
    sensor_height_px: int
    max_fps: float | None = None
    trigger_modes: tuple[str, ...] = ("software",)


class Camera(ABC):
    @abstractmethod
    def open(self) -> None:
        """Open the connection to the camera."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection to the camera."""

    @abstractmethod
    def configure(self, settings: CameraSettings) -> None:
        """Apply exposure/gain/pixel-format/binning settings."""

    @abstractmethod
    def acquire_frame(self, timeout_ms: int) -> Frame:
        """Synchronously grab a single frame."""

    @abstractmethod
    def device_name(self) -> str:
        """Return a user-facing device identifier."""

    @abstractmethod
    def capabilities(self) -> CameraCapabilities:
        """Return this camera's real resolution/fps/trigger-mode limits.

        No default here (unlike lspr_acq_shell's Spectrometer.capabilities()):
        a fabricated 0x0-sensor default would silently misrepresent real
        hardware, whereas Spectrometer's all-flags-off default is a genuinely
        meaningful "no optional features" answer.
        """
