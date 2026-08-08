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
class ImagingAcquisitionSettings:
    wavelengths_nm: list[float]
    exposure_us: float
    gain: float | None = None
    # None means: use illumination.settle_time_ms() for every step.
    settle_time_override_ms: float | None = None


@dataclass(slots=True)
class AbsorbanceSpectrumResult:
    roi_id: int
    wavelengths_nm: np.ndarray
    absorbance: np.ndarray
    cube_index: int
