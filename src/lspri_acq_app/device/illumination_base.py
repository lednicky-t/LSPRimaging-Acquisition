"""Illumination-source device abstraction.

Two real protocols inform this ABC (see the architecture plan section 6):
VariSpec is continuously tunable (any nm within its range); the Lori-protocol
LED array is discrete-channel (a fixed set of LED channels, each with a
nominal wavelength). set_wavelength() on a channel-based device resolves to
"nearest configured channel" - the ABC deliberately does not assume every
illumination source is continuously tunable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IlluminationSourceError(RuntimeError):
    """Raised when an illumination-source operation fails."""


class IlluminationSource(ABC):
    @abstractmethod
    def open(self) -> None:
        """Open the connection to the illumination source."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection to the illumination source."""

    @abstractmethod
    def set_wavelength(self, nm: float) -> None:
        """Tune to the given wavelength (or the nearest available channel)."""

    @abstractmethod
    def current_wavelength(self) -> float | None:
        """Return the last wavelength set, or None if never set."""

    @abstractmethod
    def wavelength_range(self) -> tuple[float, float] | None:
        """Return (min_nm, max_nm) for continuously tunable devices, or None
        for discrete-channel devices with no single continuous range."""

    @abstractmethod
    def settle_time_ms(self) -> float:
        """How long to wait after set_wavelength() before output is stable
        enough to trigger a camera frame.

        No default here: a wrong default (e.g. 0) would let a sweep grab a
        frame before the filter/LEDs have actually settled, silently
        corrupting real data - each backend must state its own real number.
        """

    @abstractmethod
    def device_name(self) -> str:
        """Return a user-facing device identifier."""
