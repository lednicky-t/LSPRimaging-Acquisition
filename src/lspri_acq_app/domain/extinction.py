"""Extinction/absorbance math for one wavelength-swept spectral cube.

Unlike singleLSPR Acquisition's compute_absorbance() (apps/sLSPR/acq/src/
lspr_app/domain/session.py), which divides a *sequential* sample spectrum by
a separately-acquired reference spectrum (same wavelength axis, different
points in time), this app's absorbance comes from two regions of the *same*
frame at each swept wavelength: an ROI's sample disk vs its own reference
annulus (see processing/roi_extraction.py). The formula
(-log10(sample/reference)) and validity gate (only where both means are
positive, NaN elsewhere) mirror that existing convention for consistency
across the suite, applied per-wavelength-step instead of per-pixel.

Deliberate v1 simplification, not an oversight: no camera dark-current/bias
subtraction step - the architecture plan's own acquisition pipeline
(section 8) goes straight from cube to per-ROI means to absorbance, with no
dark-frame concept for imaging acquisition. Revisit if photometric accuracy
at low signal levels turns out to need it.
"""

from __future__ import annotations

import numpy as np

from lspri_acq_app.domain.models import AbsorbanceSpectrumResult


def absorbance_from_means(sample_means: np.ndarray, reference_means: np.ndarray) -> np.ndarray:
    sample_means = np.asarray(sample_means, dtype=np.float64)
    reference_means = np.asarray(reference_means, dtype=np.float64)
    if sample_means.shape != reference_means.shape:
        raise ValueError(
            f"sample_means shape {sample_means.shape} != reference_means shape {reference_means.shape}"
        )
    valid = np.isfinite(sample_means) & np.isfinite(reference_means) & (sample_means > 0.0) & (reference_means > 0.0)
    absorbance = np.full_like(sample_means, np.nan, dtype=np.float64)
    absorbance[valid] = -np.log10(sample_means[valid] / reference_means[valid])
    return absorbance


def build_absorbance_spectrum_result(
    *,
    roi_id: int,
    cube_index: int,
    wavelengths_nm: np.ndarray,
    sample_means: np.ndarray,
    reference_means: np.ndarray,
) -> AbsorbanceSpectrumResult:
    return AbsorbanceSpectrumResult(
        roi_id=roi_id,
        wavelengths_nm=np.asarray(wavelengths_nm, dtype=np.float64).copy(),
        absorbance=absorbance_from_means(sample_means, reference_means),
        cube_index=cube_index,
    )


def peak_absorbance(wavelengths_nm: np.ndarray, absorbance: np.ndarray) -> tuple[float, float] | None:
    """Return (wavelength_nm, value) at the highest finite absorbance point."""
    finite = np.isfinite(absorbance)
    if not np.any(finite):
        return None
    candidate_values = np.where(finite, absorbance, -np.inf)
    index = int(np.argmax(candidate_values))
    return float(wavelengths_nm[index]), float(absorbance[index])


def centroid_wavelength(wavelengths_nm: np.ndarray, absorbance: np.ndarray) -> float | None:
    """Intensity-weighted centroid wavelength, weighted above the curve's
    own minimum so only signal above baseline contributes - the same
    baseline-referenced-weighted-mean idea as singleLSPR Acquisition's
    centroid_from_curve() (apps/sLSPR/acq/src/lspr_app/domain/processing.py),
    reimplemented simply here without that function's extra
    threshold_fraction/legacy-mode parameters, which this app doesn't need
    yet.
    """
    finite_mask = np.isfinite(absorbance)
    if np.count_nonzero(finite_mask) < 2:
        return None
    wavelengths_nm = np.asarray(wavelengths_nm, dtype=np.float64)[finite_mask]
    values = np.asarray(absorbance, dtype=np.float64)[finite_mask]
    baseline = float(np.min(values))
    weights = values - baseline
    total_weight = float(np.sum(weights))
    if total_weight <= 0.0:
        return None
    return float(np.sum(wavelengths_nm * weights) / total_weight)
