"""Core domain model for a wavelength-sweep imaging acquisition.

See docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md
section 7 in the umbrella repo for the design this mirrors. Kept Qt-free so it
can be used by device/, processing/, and storage/ code without pulling in a
running Qt application (matches the suite-wide "don't mix scientific code with
GUI code" rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass(slots=True)
class Frame:
    """One camera grab at (nominally) one illumination wavelength.

    wavelength_nm is filled in by the sweep controller after acquire_frame()
    returns - the camera itself has no way to know what wavelength the
    illumination source was set to, so a freshly-acquired Frame carries
    float("nan") until the sweep loop assigns the real value.
    """

    image: np.ndarray
    wavelength_nm: float
    acquired_at: datetime
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SpectralCube:
    """One completed wavelength sweep: one Frame per swept wavelength, in order."""

    frames: list[Frame]
    cube_index: int
    started_at: datetime
    completed_at: datetime


@dataclass(slots=True)
class WavelengthCameraSettings:
    """Per-wavelength camera config, overriding ImagingAcquisitionSettings'
    global exposure_us/gain for one wavelength - added 2026-08-09 so exposure
    (and, later, binning/crop) can vary per wavelength for good contrast at
    each color, per the maintainer's session-recording workflow description.
    Fields left None/default fall back to the sensor's current setting
    (resolution/crop) or aren't overridden (binning)."""

    exposure_us: float
    gain: float | None = None
    binning: int = 1
    resolution_width_px: int | None = None
    resolution_height_px: int | None = None
    crop_x_px: int | None = None
    crop_y_px: int | None = None
    crop_width_px: int | None = None
    crop_height_px: int | None = None
    saving_mode: str = "all_frames"


@dataclass(slots=True)
class WavelengthIlluminationSettings:
    """Per-wavelength illumination config - settle time, LED current, and
    which spectrum (measured vs. a default pre-measured file) this
    wavelength's entry uses. Added alongside WavelengthCameraSettings, same
    2026-08-09 design discussion."""

    settle_time_ms: float | None = None
    current: float | None = None
    spectrum_source: str = "default_file"


@dataclass(slots=True)
class ImagingAcquisitionSettings:
    wavelengths_nm: list[float]
    exposure_us: float
    gain: float | None = None
    # None means: use illumination.settle_time_ms() for every step.
    settle_time_override_ms: float | None = None
    # Per-wavelength overrides, keyed by the exact value in wavelengths_nm.
    # A wavelength absent from these dicts falls back to exposure_us/gain and
    # settle_time_override_ms/illumination.settle_time_ms() above - existing
    # callers that never set these two dicts keep today's global-only
    # behavior unchanged.
    camera_settings_by_wavelength: dict[float, WavelengthCameraSettings] = field(default_factory=dict)
    illumination_settings_by_wavelength: dict[float, WavelengthIlluminationSettings] = field(default_factory=dict)


@dataclass(slots=True)
class AbsorbanceSpectrumResult:
    roi_id: int
    wavelengths_nm: np.ndarray
    absorbance: np.ndarray
    cube_index: int
