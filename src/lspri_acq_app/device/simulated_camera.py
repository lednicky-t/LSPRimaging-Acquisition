"""Development camera backend, no hardware required.

Mirrors SimulatedSpectrometer's role in singleLSPR Acquisition: lets the full
sweep -> cube -> extinction -> sensorgram pipeline run in tests and
simulation mode with no camera attached.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from lspri_acq_app.device.camera_base import Camera, CameraCapabilities, CameraSettings
from lspri_acq_app.domain.models import Frame


class SimulatedCamera(Camera):
    """Returns synthetic frames: a configurable set of Gaussian spots on a
    noisy background, at a fixed sensor size."""

    def __init__(
        self,
        *,
        width_px: int = 640,
        height_px: int = 480,
        spot_centers_px: tuple[tuple[float, float], ...] = ((320.0, 240.0),),
        spot_sigma_px: float = 60.0,
        peak_intensity: float = 3000.0,
        noise_std: float = 15.0,
        max_count: float = 4095.0,
    ) -> None:
        self._width_px = width_px
        self._height_px = height_px
        self._spot_centers_px = spot_centers_px
        self._spot_sigma_px = spot_sigma_px
        self._peak_intensity = peak_intensity
        self._noise_std = noise_std
        self._max_count = max_count
        self._is_open = False
        self._settings = CameraSettings(exposure_us=10_000.0)
        yy, xx = np.mgrid[0:height_px, 0:width_px]
        self._yy = yy.astype(np.float64)
        self._xx = xx.astype(np.float64)

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    def configure(self, settings: CameraSettings) -> None:
        self._settings = settings

    def acquire_frame(self, timeout_ms: int) -> Frame:
        if not self._is_open:
            raise RuntimeError("SimulatedCamera.acquire_frame() called before open()")
        image = np.zeros((self._height_px, self._width_px), dtype=np.float64)
        for center_x, center_y in self._spot_centers_px:
            image += self._peak_intensity * np.exp(
                -((self._xx - center_x) ** 2 + (self._yy - center_y) ** 2)
                / (2.0 * self._spot_sigma_px**2)
            )
        if self._noise_std > 0:
            image += np.random.normal(0.0, self._noise_std, size=image.shape)
        image = np.clip(image, 0.0, self._max_count).astype(np.uint16)
        return Frame(
            image=image,
            wavelength_nm=float("nan"),
            acquired_at=datetime.now(timezone.utc),
            metadata={
                "device": self.device_name(),
                "exposure_us": self._settings.exposure_us,
                "gain": self._settings.gain,
                "pixel_format": self._settings.pixel_format,
                "binning": self._settings.binning,
                "mode": "simulated",
            },
        )

    def device_name(self) -> str:
        return "Simulated Camera"

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            sensor_width_px=self._width_px,
            sensor_height_px=self._height_px,
            max_fps=None,
            trigger_modes=("software",),
        )
