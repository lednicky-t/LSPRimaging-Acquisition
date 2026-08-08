"""Development illumination-source backend, no hardware required.

Instant tuning, zero settle time - mirrors SimulatedCamera's role.
"""

from __future__ import annotations

from lspri_acq_app.device.illumination_base import IlluminationSource


class SimulatedIllumination(IlluminationSource):
    def __init__(self, wavelength_range_nm: tuple[float, float] = (400.0, 720.0)) -> None:
        self._wavelength_range_nm = wavelength_range_nm
        self._current_wavelength_nm: float | None = None
        self._is_open = False

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False
        self._current_wavelength_nm = None

    def set_wavelength(self, nm: float) -> None:
        if not self._is_open:
            raise RuntimeError("SimulatedIllumination.set_wavelength() called before open()")
        low, high = self._wavelength_range_nm
        if not (low <= nm <= high):
            raise ValueError(f"{nm} nm is outside the simulated range {self._wavelength_range_nm}")
        self._current_wavelength_nm = nm

    def current_wavelength(self) -> float | None:
        return self._current_wavelength_nm

    def wavelength_range(self) -> tuple[float, float] | None:
        return self._wavelength_range_nm

    def settle_time_ms(self) -> float:
        return 0.0

    def device_name(self) -> str:
        return "Simulated Illumination"
