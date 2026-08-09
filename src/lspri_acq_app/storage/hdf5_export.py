"""HDF5 session/measurement writer for LSPRimaging Acquisition.

Writes into `lspr_measurement` v6.4 (see packages/lspr_io/src/lspr_io/schema.py's
2026-08-09 changelog entry and docs/architecture/general/lspri_acq_build_log.md's
same-dated entry for the full design discussion). Deliberately reuses the shared
`lspr_io` identity/versioning helpers and the newly-generalized `upsert_table`
(promoted 2026-08-09 from sLSPR acq's private `HDF5MeasurementWriter._upsert_table`)
rather than re-deriving them - this app's own root/manifest metadata is
byte-for-byte the same shape sLSPR acq's writer produces.

**A "session" and a "measurement" are the same file format.** A session save is
just a file with the setup groups below populated and zero raw rows -
`has_recorded_data` (a metadata attr) is the only thing distinguishing "pure
setup snapshot, still editable" from "has actually recorded data." This was the
maintainer's own framing during the design discussion ("restoring new session, or
loading of those files") and avoids inventing a second schema.

**What this module does NOT do**: write image pixel data (that's
`storage/image_writer.py`'s `TiffCubeWriter`/`OmeZarrCubeWriter` - this writer only
records a manifest table pointing at wherever those wrote to) or write the
experiment-control plan table (`lspr_acq_shell.experiment_control_export`'s
`ExperimentPlanExportTask` already covers plan im/export via
`lspr_io.build_experiment_plan_row_table`; wiring that path into a live session
file is a GUI-integration follow-up, not a writer capability that's missing).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from lspr_io import (
    LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME,
    LSPR_MEASUREMENT_CAMERA_SETTINGS_COLUMNS,
    LSPR_MEASUREMENT_CAMERA_SETTINGS_DATASET_NAME,
    LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME,
    LSPR_MEASUREMENT_HAS_RECORDED_DATA_ATTR,
    LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_COLUMNS,
    LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_DATASET_NAME,
    LSPR_MEASUREMENT_ILLUMINATION_SPECTRA_GROUP_NAME,
    LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_COLUMNS,
    LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_DATASET_NAME,
    LSPR_MEASUREMENT_ROI_DEFINITIONS_COLUMNS,
    LSPR_MEASUREMENT_ROI_DEFINITIONS_DATASET_NAME,
    LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME,
    LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME,
    LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME,
    LSPR_PROCESSED_SENSORGRAM_COLUMNS,
    LSPR_PROCESSED_SENSORGRAM_GROUP_NAME,
    read_string_table_dataset,
    standard_measurement_metadata,
    upsert_table,
    write_measurement_manifest_metadata,
    write_measurement_root_metadata,
)

from lspri_acq_app.domain.models import (
    ImagingAcquisitionSettings,
    WavelengthCameraSettings,
    WavelengthIlluminationSettings,
)
from lspri_acq_app.domain.roi import AreaRoi, AreaRoiGroup
from lspri_acq_app.version import APP_NAME, APP_VERSION


def _opt(value: object) -> str:
    """Empty string for None, str() otherwise - upsert_table's rows are all
    strings (h5py string-dtype table), and None has no natural string form
    that round-trips unambiguously as "was never set" vs. "was set to the
    string 'None'"."""
    return "" if value is None else str(value)


def _wavelength_group_name(wavelength_nm: float) -> str:
    return f"{float(wavelength_nm):g}"


class ImagingMeasurementWriter:
    """Owns one open v6.4 measurement/session HDF5 file. Setup-time
    (illumination/camera/ROI/assignment-table) methods overwrite their table
    in place - call again after an edit, same idiom as sLSPR acq's writer.
    append_* methods grow a dataset one row at a time, for data produced
    live during a running measurement.
    """

    def __init__(
        self,
        path: Path,
        *,
        experiment_name: str = "",
        started_at_utc: datetime | None = None,
    ) -> None:
        self.path = Path(path)
        self._started_at_utc = started_at_utc or datetime.now(timezone.utc)
        self._handle = h5py.File(self.path, "w")

        identity_kwargs = dict(
            created_by=APP_NAME,
            started_at_utc=self._started_at_utc,
            app_name=APP_NAME,
            app_version=APP_VERSION,
            experiment_name=experiment_name,
        )
        write_measurement_root_metadata(self._handle, **standard_measurement_metadata(**identity_kwargs))
        self._manifest = self._handle.create_group("manifest")
        write_measurement_manifest_metadata(
            self._manifest,
            **standard_measurement_metadata(**identity_kwargs),
            storage_compression_enabled=False,
            storage_compression_filter="none",
            storage_compression_level=0,
            extra_attrs={"manifest_kind": "measurement"},
        )

        self._data = self._handle.create_group("data")
        self._metadata = self._handle.create_group("metadata")
        self._processed = self._handle.create_group("processed")
        self._assignment_tables = self._metadata.create_group(LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME)
        self._illumination_spectra = self._metadata.create_group(LSPR_MEASUREMENT_ILLUMINATION_SPECTRA_GROUP_NAME)
        self._metadata.attrs[LSPR_MEASUREMENT_HAS_RECORDED_DATA_ATTR] = False

        self._image_cube_manifest_rows: list[list[str]] = []
        self._roi_absorbance_groups: dict[int, h5py.Group] = {}
        self._roi_sensorgram_groups: dict[int, h5py.Group] = {}

    # -- setup-time metadata: illumination / camera / ROI ------------------

    def write_illumination_settings(self, settings: ImagingAcquisitionSettings) -> None:
        rows: list[list[str]] = []
        for wavelength_nm in settings.wavelengths_nm:
            override = settings.illumination_settings_by_wavelength.get(wavelength_nm)
            settle_ms = override.settle_time_ms if override is not None else None
            if settle_ms is None:
                settle_ms = settings.settle_time_override_ms
            current = override.current if override is not None else None
            spectrum_source = override.spectrum_source if override is not None else "default_file"
            rows.append([_opt(wavelength_nm), _opt(settle_ms), _opt(current), spectrum_source])
        upsert_table(
            self._metadata,
            LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_DATASET_NAME,
            rows,
            LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_COLUMNS,
        )

    def write_illumination_spectrum(
        self, wavelength_nm: float, spectrum_wavelengths_nm: np.ndarray, spectrum_values: np.ndarray
    ) -> None:
        """One measured (or default-file) spectrum for one swept wavelength -
        joined back to metadata/illumination_settings by wavelength_nm."""
        name = _wavelength_group_name(wavelength_nm)
        if name in self._illumination_spectra:
            del self._illumination_spectra[name]
        group = self._illumination_spectra.create_group(name)
        group.create_dataset("wavelengths_nm", data=np.asarray(spectrum_wavelengths_nm, dtype=np.float64))
        group.create_dataset("values", data=np.asarray(spectrum_values, dtype=np.float64))
        group.attrs["wavelength_nm"] = float(wavelength_nm)

    def write_camera_settings(self, settings: ImagingAcquisitionSettings) -> None:
        rows: list[list[str]] = []
        for wavelength_nm in settings.wavelengths_nm:
            override = settings.camera_settings_by_wavelength.get(wavelength_nm)
            if override is not None:
                rows.append(
                    [
                        _opt(wavelength_nm),
                        _opt(override.exposure_us),
                        _opt(override.gain),
                        _opt(override.binning),
                        _opt(override.resolution_width_px),
                        _opt(override.resolution_height_px),
                        _opt(override.crop_x_px),
                        _opt(override.crop_y_px),
                        _opt(override.crop_width_px),
                        _opt(override.crop_height_px),
                        override.saving_mode,
                    ]
                )
            else:
                rows.append(
                    [
                        _opt(wavelength_nm),
                        _opt(settings.exposure_us),
                        _opt(settings.gain),
                        "1",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "all_frames",
                    ]
                )
        upsert_table(
            self._metadata,
            LSPR_MEASUREMENT_CAMERA_SETTINGS_DATASET_NAME,
            rows,
            LSPR_MEASUREMENT_CAMERA_SETTINGS_COLUMNS,
        )

    def write_roi_definitions(self, rois: list[AreaRoi], groups: list[AreaRoiGroup] | None = None) -> None:
        group_id_by_roi_id: dict[int, str] = {}
        for group in groups or []:
            for roi_id in group.area_roi_ids:
                group_id_by_roi_id[roi_id] = group.group_id
        rows: list[list[str]] = []
        for roi in rois:
            rows.append(
                [
                    _opt(roi.area_roi_id),
                    group_id_by_roi_id.get(roi.area_roi_id, ""),
                    _opt(roi.center_x),
                    _opt(roi.center_y),
                    _opt(roi.sample_radius_px),
                    _opt(roi.sample_diameter_px),
                    _opt(roi.reference_inner_diameter_px),
                    _opt(roi.reference_outer_diameter_px),
                    roi.sample_color_hex or "",
                    roi.reference_color_hex or "",
                ]
            )
        upsert_table(
            self._processed,
            LSPR_MEASUREMENT_ROI_DEFINITIONS_DATASET_NAME,
            rows,
            LSPR_MEASUREMENT_ROI_DEFINITIONS_COLUMNS,
        )

    # -- assignment tables: same tables/columns sLSPR acq writes, for the
    # maintainer's requested plan/valve/switch/color-palette compatibility --

    def write_valve_state_labels(self, labels: dict[str, str], colors: dict[str, str] | None = None) -> None:
        rows: list[list[str]] = []
        for state_name, default_color in (("Open", "#4E79A7"), ("Close", "#B44A4A")):
            label = str(labels.get(state_name, state_name)).strip() or state_name
            color = str((colors or {}).get(state_name, default_color)).strip().upper() or default_color
            rows.append([state_name, label, color])
        upsert_table(self._assignment_tables, LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME, rows, ["state", "label", "color"])

    def write_color_palette_entries(self, entries: list[tuple[str, str]]) -> None:
        rows: list[list[str]] = []
        for index, (name, color) in enumerate(entries):
            color = str(color or "").strip().upper()
            if not color:
                continue
            rows.append([str(name or f"Custom {index + 1}").strip() or f"Custom {index + 1}", color])
        upsert_table(
            self._assignment_tables, LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME, rows, ["name", "color"]
        )

    def write_switch_solution_labels(self, labels: list[str]) -> None:
        rows = [[str(index + 1), label] for index, label in enumerate(labels) if str(label).strip()]
        upsert_table(
            self._assignment_tables,
            LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME,
            rows,
            ["switch_port", "solution_label"],
        )

    # -- live/appended data: image manifest, sensorgram, absorbance --------

    def append_image_cube_manifest_row(self, *, cube_index: int, timestamp_utc_ms: int, file_path: str) -> None:
        self._image_cube_manifest_rows.append([str(cube_index), str(timestamp_utc_ms), file_path])
        upsert_table(
            self._metadata,
            LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_DATASET_NAME,
            self._image_cube_manifest_rows,
            LSPR_MEASUREMENT_IMAGE_CUBE_MANIFEST_COLUMNS,
        )

    def _roi_sensorgram_group(self, roi_id: int) -> h5py.Group:
        group = self._roi_sensorgram_groups.get(roi_id)
        if group is not None:
            return group
        parent = self._processed.require_group(LSPR_PROCESSED_SENSORGRAM_GROUP_NAME)
        group = parent.create_group(str(roi_id))
        group.create_dataset("timestamp_utc_ms", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True)
        group.create_dataset("metric_value", shape=(0,), maxshape=(None,), dtype=np.float64, chunks=True)
        group.attrs["columns"] = np.asarray(LSPR_PROCESSED_SENSORGRAM_COLUMNS, dtype=h5py.string_dtype(encoding="utf-8"))
        self._roi_sensorgram_groups[roi_id] = group
        return group

    def append_sensorgram_point(self, *, roi_id: int, timestamp_utc_ms: int, metric_value: float) -> None:
        group = self._roi_sensorgram_group(roi_id)
        _append_scalar(group["timestamp_utc_ms"], timestamp_utc_ms)
        _append_scalar(group["metric_value"], metric_value)

    def _roi_absorbance_group(self, roi_id: int, *, n_wavelengths: int) -> h5py.Group:
        group = self._roi_absorbance_groups.get(roi_id)
        if group is not None:
            return group
        parent = self._processed.require_group(LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME)
        group = parent.create_group(str(roi_id))
        group.create_dataset("cube_index", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True)
        group.create_dataset(
            "absorbance", shape=(0, n_wavelengths), maxshape=(None, n_wavelengths), dtype=np.float32, chunks=True
        )
        self._roi_absorbance_groups[roi_id] = group
        return group

    def append_absorbance_spectrum(
        self, *, roi_id: int, wavelengths_nm: np.ndarray, absorbance: np.ndarray, cube_index: int
    ) -> None:
        wavelengths_nm = np.asarray(wavelengths_nm, dtype=np.float64)
        absorbance = np.asarray(absorbance, dtype=np.float32)
        group = self._roi_absorbance_group(roi_id, n_wavelengths=len(wavelengths_nm))
        if "wavelengths_nm" not in group:
            group.create_dataset("wavelengths_nm", data=wavelengths_nm)
        _append_scalar(group["cube_index"], cube_index)
        _append_row(group["absorbance"], absorbance)

    # -- lifecycle -----------------------------------------------------------

    def mark_recording_started(self) -> None:
        self._metadata.attrs[LSPR_MEASUREMENT_HAS_RECORDED_DATA_ATTR] = True

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "ImagingMeasurementWriter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _append_scalar(dataset: h5py.Dataset, value: object) -> None:
    index = dataset.shape[0]
    dataset.resize((index + 1,) + dataset.shape[1:])
    dataset[index] = value


def _append_row(dataset: h5py.Dataset, row: np.ndarray) -> None:
    index = dataset.shape[0]
    dataset.resize((index + 1,) + dataset.shape[1:])
    dataset[index, :] = row


@dataclass(slots=True)
class ImagingSessionSnapshot:
    """Everything read_imaging_session() restores from a v6.4 file - enough
    to repopulate the illumination/camera setup and ROI placement a user had
    before closing the app, whether the file is a pure session save or a
    previously recorded measurement. Does NOT include the experiment-control
    plan table or live sensorgram/absorbance data - restoring those is a
    separate, not-yet-wired GUI concern (see this module's own docstring)."""

    settings: ImagingAcquisitionSettings
    rois: list[AreaRoi]
    roi_groups: list[AreaRoiGroup]
    valve_state_labels: dict[str, str]
    valve_state_colors: dict[str, str]
    color_palette_entries: list[tuple[str, str]]
    switch_solution_labels: list[str]
    has_recorded_data: bool


def read_imaging_session(path: Path) -> ImagingSessionSnapshot:
    """Reads back everything ImagingMeasurementWriter's setup-time methods
    wrote - the read-side counterpart making a v6.4 file usable as a real
    session file, not just a write-only recording target."""
    with h5py.File(path, "r") as handle:
        metadata = handle["metadata"]
        processed = handle.get("processed")
        has_recorded_data = bool(metadata.attrs.get(LSPR_MEASUREMENT_HAS_RECORDED_DATA_ATTR, False))

        wavelengths_nm: list[float] = []
        illumination_by_wavelength: dict[float, WavelengthIlluminationSettings] = {}
        _columns, illumination_rows = read_string_table_dataset(
            metadata.get(LSPR_MEASUREMENT_ILLUMINATION_SETTINGS_DATASET_NAME)
        )
        for row in illumination_rows:
            wavelength_nm = float(row[0])
            wavelengths_nm.append(wavelength_nm)
            illumination_by_wavelength[wavelength_nm] = WavelengthIlluminationSettings(
                settle_time_ms=float(row[1]) if row[1] else None,
                current=float(row[2]) if row[2] else None,
                spectrum_source=row[3] or "default_file",
            )

        camera_by_wavelength: dict[float, WavelengthCameraSettings] = {}
        exposure_us = 0.0
        gain: float | None = None
        _columns, camera_rows = read_string_table_dataset(metadata.get(LSPR_MEASUREMENT_CAMERA_SETTINGS_DATASET_NAME))
        for index, row in enumerate(camera_rows):
            wavelength_nm = float(row[0])
            row_exposure_us = float(row[1])
            row_gain = float(row[2]) if row[2] else None
            camera_by_wavelength[wavelength_nm] = WavelengthCameraSettings(
                exposure_us=row_exposure_us,
                gain=row_gain,
                binning=int(row[3]) if row[3] else 1,
                resolution_width_px=int(row[4]) if row[4] else None,
                resolution_height_px=int(row[5]) if row[5] else None,
                crop_x_px=int(row[6]) if row[6] else None,
                crop_y_px=int(row[7]) if row[7] else None,
                crop_width_px=int(row[8]) if row[8] else None,
                crop_height_px=int(row[9]) if row[9] else None,
                saving_mode=row[10] or "all_frames",
            )
            if wavelength_nm not in wavelengths_nm:
                wavelengths_nm.append(wavelength_nm)  # camera_settings has a wavelength illumination_settings didn't
            if index == 0:
                exposure_us, gain = row_exposure_us, row_gain

        settings = ImagingAcquisitionSettings(
            wavelengths_nm=wavelengths_nm,
            exposure_us=exposure_us,
            gain=gain,
            camera_settings_by_wavelength=camera_by_wavelength,
            illumination_settings_by_wavelength=illumination_by_wavelength,
        )

        rois: list[AreaRoi] = []
        roi_groups_by_id: dict[str, AreaRoiGroup] = {}
        roi_dataset = processed.get(LSPR_MEASUREMENT_ROI_DEFINITIONS_DATASET_NAME) if processed is not None else None
        _columns, roi_rows = read_string_table_dataset(roi_dataset)
        for row in roi_rows:
            area_roi_id = int(row[0])
            group_id = row[1]
            rois.append(
                AreaRoi(
                    area_roi_id=area_roi_id,
                    center_x=float(row[2]),
                    center_y=float(row[3]),
                    sample_radius_px=float(row[4]),
                    sample_diameter_px=float(row[5]) if row[5] else None,
                    reference_inner_diameter_px=float(row[6]) if row[6] else None,
                    reference_outer_diameter_px=float(row[7]) if row[7] else None,
                    sample_color_hex=row[8] or None,
                    reference_color_hex=row[9] or None,
                )
            )
            if group_id:
                group = roi_groups_by_id.setdefault(group_id, AreaRoiGroup(group_id=group_id, name=group_id))
                group.area_roi_ids.append(area_roi_id)

        assignment_tables = metadata.get(LSPR_MEASUREMENT_ASSIGNMENT_TABLES_GROUP_NAME)
        valve_state_labels: dict[str, str] = {}
        valve_state_colors: dict[str, str] = {}
        color_palette_entries: list[tuple[str, str]] = []
        switch_solution_labels: list[str] = []
        if assignment_tables is not None:
            _columns, valve_rows = read_string_table_dataset(
                assignment_tables.get(LSPR_MEASUREMENT_VALVE_STATE_MAP_DATASET_NAME)
            )
            for row in valve_rows:
                valve_state_labels[row[0]] = row[1]
                valve_state_colors[row[0]] = row[2]

            _columns, palette_rows = read_string_table_dataset(
                assignment_tables.get(LSPR_MEASUREMENT_COLOR_PALETTE_ENTRIES_DATASET_NAME)
            )
            color_palette_entries = [(row[0], row[1]) for row in palette_rows]

            _columns, switch_rows = read_string_table_dataset(
                assignment_tables.get(LSPR_MEASUREMENT_SWITCH_SOLUTION_MAP_DATASET_NAME)
            )
            by_port = {int(row[0]): row[1] for row in switch_rows}
            if by_port:
                switch_solution_labels = [by_port.get(port, "") for port in range(1, max(by_port) + 1)]

        return ImagingSessionSnapshot(
            settings=settings,
            rois=rois,
            roi_groups=list(roi_groups_by_id.values()),
            valve_state_labels=valve_state_labels,
            valve_state_colors=valve_state_colors,
            color_palette_entries=color_palette_entries,
            switch_solution_labels=switch_solution_labels,
            has_recorded_data=has_recorded_data,
        )
