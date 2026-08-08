"""ROI editing geometry helpers, for AreaRoi (Qt-free).

Not a port of LSPRimaging Evaluation's domain/roi_editor_tools.py, despite
the architecture plan's section 10 describing that file's helpers as
"reusable as-is" - checked before assuming that held. Every clone/move
function there (clamp_center_to_image, move_rectangle_roi,
clone_rectangle_template, ...) is built around RoiDefinition (rectangle/
ellipse, size_x/size_y), a different type from this app's AreaRoi (sample
disk + reference annulus, sample_radius_px) - not directly reusable.
build_grid_positions() there IS fully generic (no RoiDefinition
dependency), but isn't needed for v1's manual-placement-only scope (no
array/grid ROI generation requested), so it isn't pulled in until an actual
use appears.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from lspri_acq_app.domain.roi import AreaRoi


def roi_outer_radius_px(roi: AreaRoi) -> float:
    """The largest radius this ROI actually occupies on screen - the
    sample disk, or the reference annulus's outer edge if configured,
    whichever is bigger. Used to clamp the ROI's center so its full visual
    extent, not just the sample disk, stays on the image."""
    radius = float(roi.sample_radius_px)
    if roi.reference_outer_diameter_px is not None:
        radius = max(radius, float(roi.reference_outer_diameter_px) / 2.0)
    return radius


def clamp_center_to_image(
    center_x: float, center_y: float, radius_px: float, image_shape: tuple[int, int] | None
) -> tuple[float, float]:
    """Clamp a circular ROI's center so its full extent (given radius_px)
    stays within image_shape's (height, width) bounds. No-op if
    image_shape is None (no image loaded yet)."""
    if image_shape is None:
        return float(center_x), float(center_y)
    image_height, image_width = int(image_shape[0]), int(image_shape[1])
    radius = max(float(radius_px), 0.5)
    x = float(np.clip(float(center_x), radius, max(float(image_width) - radius, radius)))
    y = float(np.clip(float(center_y), radius, max(float(image_height) - radius, radius)))
    return x, y


def move_roi(
    roi: AreaRoi, *, center_x: float, center_y: float, image_shape: tuple[int, int] | None = None
) -> None:
    """Move roi to a new center, clamped to stay within image_shape.

    Mutates roi in place (AreaRoi is a plain mutable dataclass, not
    frozen) rather than returning a copy - matches how a live ROI panel
    updates one specific AreaRoi instance already held in a list during a
    drag gesture (many updates per drag), not a copy-on-write list
    replacement pattern.
    """
    radius = roi_outer_radius_px(roi)
    roi.center_x, roi.center_y = clamp_center_to_image(center_x, center_y, radius, image_shape)


def next_area_roi_id(existing_ids: Iterable[int]) -> int:
    """Smallest positive integer not already in existing_ids - mirrors
    communication_models.next_device_label()'s "smallest unused number"
    idiom (lspr_acq_shell) for the same "assign the next free slot" need,
    applied to integer ROI ids instead of string device labels."""
    used = set(existing_ids)
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate
