"""Per-cube ROI processing: ties roi_extraction.py and domain/extinction.py
together into the "for each ROI, extract -> build absorbance spectrum ->
compute a metric" step from the architecture plan's section 8 pipeline.

Deliberately returns/reports results via a callback rather than owning a
sensorgram data structure itself - no sensorgram GUI panel exists yet
(section 10), so this module has no business deciding what
"sensorgram.append_point" means. The sweep pipeline (acquisition/
sweep_pipeline.py) calls this per cube; a future sensorgram panel supplies
the callback.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

import numpy as np

from lspri_acq_app.domain.extinction import build_absorbance_spectrum_result, peak_absorbance
from lspri_acq_app.domain.models import AbsorbanceSpectrumResult, SpectralCube
from lspri_acq_app.domain.roi import AreaRoi
from lspri_acq_app.processing.roi_extraction import RoiMaskCache, extract_roi_means

CubeResultCallback = Callable[[int, datetime, AbsorbanceSpectrumResult, float | None], None]
"""(roi_id, cube.completed_at, absorbance_result, peak_metric_value_or_None)"""


def process_cube_for_rois(
    cube: SpectralCube,
    rois: Iterable[AreaRoi],
    mask_cache: RoiMaskCache,
    *,
    on_result: CubeResultCallback,
) -> None:
    if not cube.frames:
        return
    image_shape = cube.frames[0].image.shape
    frame_count = len(cube.frames)

    for roi in rois:
        mask_set = mask_cache.get(roi, image_shape)
        wavelengths_nm = np.empty(frame_count, dtype=np.float64)
        sample_means = np.empty(frame_count, dtype=np.float64)
        reference_means = np.full(frame_count, np.nan, dtype=np.float64)

        for index, frame in enumerate(cube.frames):
            sample_mean, reference_mean = extract_roi_means(frame.image, mask_set)
            wavelengths_nm[index] = frame.wavelength_nm
            sample_means[index] = sample_mean
            if reference_mean is not None:
                reference_means[index] = reference_mean

        result = build_absorbance_spectrum_result(
            roi_id=roi.area_roi_id,
            cube_index=cube.cube_index,
            wavelengths_nm=wavelengths_nm,
            sample_means=sample_means,
            reference_means=reference_means,
        )
        metric = peak_absorbance(result.wavelengths_nm, result.absorbance)
        metric_value = metric[1] if metric is not None else None
        on_result(roi.area_roi_id, cube.completed_at, result, metric_value)
