"""ROI mean extraction: cached, bounding-box-cropped masks.

A full-image-sized boolean mask (image[full_size_mask]) is O(total image
pixels) per ROI regardless of ROI size - numpy has to scan every element of
the mask to gather the True positions. Phase 0 measured this costing ~27ms
avg for just 10 small ROIs on an 8.3MP frame (almost the whole frame
period); cropping to each ROI's small bounding box first, then masking only
that sub-array, measured ~60x faster (see the architecture plan's "Phase 0
results" section and spikes/lspri_acq_phase0/benchmark_ui.py's RoiMasks/
extract_roi_means, the validated reference this module's masking approach
follows).

Deliberately not a port of LSPRimaging Evaluation's own processing/roi.py:
that module's RoiMasks still uses the O(image size) approach (np.indices
over the full image shape) - the exact bug Phase 0 found and fixed - and it
extracts against RoiDefinition (a different, rectangle/ellipse type with
generic background padding), not AreaRoi's sample-disk + reference-annulus
geometry. This module builds AreaRoi-shaped masks with the fixed,
bounding-box-cropped approach instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from lspri_acq_app.domain.roi import AreaRoi


@dataclass(slots=True, frozen=True)
class RoiMaskSet:
    """Cached bounding-box-cropped masks for one AreaRoi's sample disk and
    reference annulus, against one specific image shape."""

    sample_box: tuple[int, int, int, int]  # y0, y1, x0, x1
    sample_mask: np.ndarray  # local, shape (y1-y0, x1-x0)
    reference_box: tuple[int, int, int, int]
    reference_mask: np.ndarray


def _disk_mask(
    center_x: float, center_y: float, radius_px: float, height: int, width: int
) -> tuple[tuple[int, int, int, int], np.ndarray]:
    radius = max(float(radius_px), 0.5)
    y0 = max(0, int(np.floor(center_y - radius)))
    y1 = min(height, int(np.ceil(center_y + radius)) + 1)
    x0 = max(0, int(np.floor(center_x - radius)))
    x1 = min(width, int(np.ceil(center_x + radius)) + 1)
    yy, xx = np.indices((max(y1 - y0, 0), max(x1 - x0, 0)), dtype=np.float64)
    local_cy = center_y - y0
    local_cx = center_x - x0
    mask = (xx - local_cx) ** 2 + (yy - local_cy) ** 2 <= radius * radius
    return (y0, y1, x0, x1), mask


def _annulus_mask(
    center_x: float, center_y: float, inner_radius_px: float, outer_radius_px: float, height: int, width: int
) -> tuple[tuple[int, int, int, int], np.ndarray]:
    inner_radius = max(float(inner_radius_px), 0.0)
    outer_radius = max(float(outer_radius_px), inner_radius + 0.5)
    y0 = max(0, int(np.floor(center_y - outer_radius)))
    y1 = min(height, int(np.ceil(center_y + outer_radius)) + 1)
    x0 = max(0, int(np.floor(center_x - outer_radius)))
    x1 = min(width, int(np.ceil(center_x + outer_radius)) + 1)
    yy, xx = np.indices((max(y1 - y0, 0), max(x1 - x0, 0)), dtype=np.float64)
    local_cy = center_y - y0
    local_cx = center_x - x0
    dist_sq = (xx - local_cx) ** 2 + (yy - local_cy) ** 2
    mask = (dist_sq <= outer_radius * outer_radius) & (dist_sq >= inner_radius * inner_radius)
    return (y0, y1, x0, x1), mask


def build_roi_mask_set(roi: AreaRoi, image_shape: tuple[int, int]) -> RoiMaskSet:
    height, width = image_shape
    sample_box, sample_mask = _disk_mask(roi.center_x, roi.center_y, roi.sample_radius_px, height, width)

    inner_diameter = roi.reference_inner_diameter_px
    outer_diameter = roi.reference_outer_diameter_px
    if inner_diameter is None or outer_diameter is None:
        reference_box = (0, 0, 0, 0)
        reference_mask = np.zeros((0, 0), dtype=bool)
    else:
        reference_box, reference_mask = _annulus_mask(
            roi.center_x, roi.center_y, inner_diameter / 2.0, outer_diameter / 2.0, height, width
        )
    return RoiMaskSet(sample_box=sample_box, sample_mask=sample_mask, reference_box=reference_box, reference_mask=reference_mask)


def extract_roi_means(image: np.ndarray, mask_set: RoiMaskSet) -> tuple[float, float | None]:
    """Returns (sample_mean, reference_mean). reference_mean is None if the
    ROI has no reference annulus configured, or the annulus has zero pixels
    within this image's bounds (e.g. an ROI near the frame edge)."""
    y0, y1, x0, x1 = mask_set.sample_box
    sample_values = image[y0:y1, x0:x1][mask_set.sample_mask]
    if sample_values.size == 0:
        raise ValueError("ROI sample region has no pixels within the image bounds.")
    sample_mean = float(sample_values.mean())

    ry0, ry1, rx0, rx1 = mask_set.reference_box
    if ry1 <= ry0 or rx1 <= rx0 or not mask_set.reference_mask.any():
        return sample_mean, None
    reference_values = image[ry0:ry1, rx0:rx1][mask_set.reference_mask]
    if reference_values.size == 0:
        return sample_mean, None
    return sample_mean, float(reference_values.mean())


class RoiMaskCache:
    """Caches RoiMaskSet per (ROI identity/geometry, image shape) - build
    masks once, reuse them every frame (the "build once, extract per frame"
    split Phase 0 measured as the fast path - see build_grid_rois vs.
    extract_roi_means in the benchmark spike this module's approach is
    based on). Rebuilds automatically if a cached ROI's geometry changes
    (the cache key includes position/radii, not just its id)."""

    def __init__(self) -> None:
        self._cache: dict[tuple, RoiMaskSet] = {}

    def get(self, roi: AreaRoi, image_shape: tuple[int, int]) -> RoiMaskSet:
        key = self._cache_key(roi, image_shape)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        mask_set = build_roi_mask_set(roi, image_shape)
        self._cache[key] = mask_set
        return mask_set

    def clear(self) -> None:
        self._cache.clear()

    @staticmethod
    def _cache_key(roi: AreaRoi, image_shape: tuple[int, int]) -> tuple:
        return (
            roi.area_roi_id,
            round(float(roi.center_x), 3),
            round(float(roi.center_y), 3),
            round(float(roi.sample_radius_px), 3),
            None if roi.reference_inner_diameter_px is None else round(float(roi.reference_inner_diameter_px), 3),
            None if roi.reference_outer_diameter_px is None else round(float(roi.reference_outer_diameter_px), 3),
            image_shape,
        )
