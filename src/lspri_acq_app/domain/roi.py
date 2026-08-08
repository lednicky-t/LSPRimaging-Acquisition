"""ROI domain types, ported from LSPRimaging Evaluation.

Ported (copied and adapted), not imported - LSPRimaging Evaluation stays fully
decoupled from this app. Kept field-for-field identical to
apps/LSPRi/eva/src/lspr_imaging_app/domain/models.py's AreaRoi/AreaRoiGroup so
a future session/file format can move ROI definitions between the two apps
without a translation layer. Some fields (score, support_*, quality_score,
inferred) exist to support eva's automated spot-detection scoring, which v1 of
this app does not do (manual ROI placement only) - they default to their
"not scored" values and are simply unused until/unless auto-detection is
added here later.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AreaRoi:
    area_roi_id: int
    center_x: float
    center_y: float
    sample_radius_px: float
    sample_color_hex: str | None = None
    reference_color_hex: str | None = None
    sample_diameter_px: float | None = None
    reference_inner_diameter_px: float | None = None
    reference_outer_diameter_px: float | None = None
    score: float = 0.0
    support_mean_radius_px: float = 0.0
    support_radius_std_px: float = 0.0
    support_value_mean: float = 0.0
    support_value_std: float = 0.0
    quality_score: float = 0.0
    inferred: bool = False


@dataclass(slots=True)
class AreaRoiGroup:
    group_id: str
    name: str
    sample_color_hex: str = "#f59e0b"
    reference_color_hex: str = "#38bdf8"
    area_roi_ids: list[int] = field(default_factory=list)
