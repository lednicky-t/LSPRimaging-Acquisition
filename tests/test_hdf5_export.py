"""Tests for storage/hdf5_export.py's ImagingMeasurementWriter - the new
v6.4 session/measurement writer (2026-08-09). See that module's own
docstring for what "session" vs. "measurement" means here (same file
format, distinguished only by has_recorded_data)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from lspr_io import read_string_table_dataset
from lspri_acq_app.domain.models import (
    ImagingAcquisitionSettings,
    WavelengthCameraSettings,
    WavelengthIlluminationSettings,
)
from lspri_acq_app.domain.roi import AreaRoi, AreaRoiGroup
from lspri_acq_app.storage.hdf5_export import ImagingMeasurementWriter, read_imaging_session


class _TempFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "session.h5"


class RootMetadataTests(_TempFileTestCase):
    def test_writes_v6_4_identity(self) -> None:
        writer = ImagingMeasurementWriter(self.path, experiment_name="demo")
        writer.close()

        with h5py.File(self.path, "r") as handle:
            self.assertEqual(handle.attrs["schema_name"], "lspr_measurement")
            self.assertEqual(handle.attrs["schema_version"], "6.4")
            self.assertEqual(handle.attrs["app_name"], "LSPRimaging Acquisition")
            for group_name in ("data", "metadata", "processed", "manifest"):
                self.assertIn(group_name, handle)

    def test_defaults_to_not_recorded(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.close()
        with h5py.File(self.path, "r") as handle:
            self.assertFalse(bool(handle["metadata"].attrs["has_recorded_data"]))

    def test_mark_recording_started_flips_the_attr(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.mark_recording_started()
        writer.close()
        with h5py.File(self.path, "r") as handle:
            self.assertTrue(bool(handle["metadata"].attrs["has_recorded_data"]))

    def test_context_manager_closes_the_file(self) -> None:
        with ImagingMeasurementWriter(self.path) as writer:
            self.assertTrue(writer._handle)
        # File must be closed - reopening for read must not raise/lock.
        with h5py.File(self.path, "r") as handle:
            self.assertIn("metadata", handle)


class IlluminationSettingsTests(_TempFileTestCase):
    def test_global_fallback_used_when_no_override(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0], exposure_us=1000.0, settle_time_override_ms=30.0
        )
        writer.write_illumination_settings(settings)
        writer.close()

        with h5py.File(self.path, "r") as handle:
            columns, rows = read_string_table_dataset(handle["metadata"]["illumination_settings"])
        self.assertEqual(columns, ["wavelength_nm", "settle_time_ms", "current", "spectrum_source"])
        self.assertEqual(rows, [["450.0", "30.0", "", "default_file"], ["500.0", "30.0", "", "default_file"]])

    def test_per_wavelength_override_wins_over_global_fallback(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0],
            exposure_us=1000.0,
            settle_time_override_ms=30.0,
            illumination_settings_by_wavelength={
                500.0: WavelengthIlluminationSettings(settle_time_ms=99.0, current=1.5, spectrum_source="measured")
            },
        )
        writer.write_illumination_settings(settings)
        writer.close()

        with h5py.File(self.path, "r") as handle:
            _columns, rows = read_string_table_dataset(handle["metadata"]["illumination_settings"])
        self.assertEqual(rows[1], ["500.0", "99.0", "1.5", "measured"])

    def test_write_illumination_settings_can_be_called_again_to_overwrite(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.write_illumination_settings(ImagingAcquisitionSettings(wavelengths_nm=[450.0], exposure_us=1000.0))
        writer.write_illumination_settings(
            ImagingAcquisitionSettings(wavelengths_nm=[450.0, 500.0, 550.0], exposure_us=1000.0)
        )
        writer.close()
        with h5py.File(self.path, "r") as handle:
            _columns, rows = read_string_table_dataset(handle["metadata"]["illumination_settings"])
        self.assertEqual(len(rows), 3)

    def test_illumination_spectrum_round_trips(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.write_illumination_spectrum(500.0, np.array([495.0, 500.0, 505.0]), np.array([0.1, 0.9, 0.2]))
        writer.close()
        with h5py.File(self.path, "r") as handle:
            group = handle["metadata"]["illumination_spectra"]["500"]
            np.testing.assert_array_equal(group["wavelengths_nm"][...], [495.0, 500.0, 505.0])
            np.testing.assert_array_equal(group["values"][...], [0.1, 0.9, 0.2])
            self.assertEqual(group.attrs["wavelength_nm"], 500.0)


class CameraSettingsTests(_TempFileTestCase):
    def test_global_fallback_and_override_both_present(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0],
            exposure_us=1000.0,
            gain=2.0,
            camera_settings_by_wavelength={
                500.0: WavelengthCameraSettings(exposure_us=9000.0, gain=3.0, binning=2, saving_mode="every_nth")
            },
        )
        writer.write_camera_settings(settings)
        writer.close()

        with h5py.File(self.path, "r") as handle:
            columns, rows = read_string_table_dataset(handle["metadata"]["camera_settings"])
        self.assertEqual(
            columns,
            [
                "wavelength_nm",
                "exposure_us",
                "gain",
                "binning",
                "resolution_width_px",
                "resolution_height_px",
                "crop_x_px",
                "crop_y_px",
                "crop_width_px",
                "crop_height_px",
                "saving_mode",
            ],
        )
        self.assertEqual(rows[0], ["450.0", "1000.0", "2.0", "1", "", "", "", "", "", "", "all_frames"])
        self.assertEqual(rows[1][:4], ["500.0", "9000.0", "3.0", "2"])
        self.assertEqual(rows[1][-1], "every_nth")


class RoiDefinitionsTests(_TempFileTestCase):
    def test_roi_rows_written_with_group_id_joined_in(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        rois = [
            AreaRoi(
                area_roi_id=1,
                center_x=10.0,
                center_y=20.0,
                sample_radius_px=5.0,
                sample_color_hex="#f59e0b",
                reference_inner_diameter_px=12.0,
                reference_outer_diameter_px=18.0,
            ),
            AreaRoi(area_roi_id=2, center_x=30.0, center_y=40.0, sample_radius_px=6.0),
        ]
        groups = [AreaRoiGroup(group_id="g1", name="Pair 1", area_roi_ids=[1])]
        writer.write_roi_definitions(rois, groups)
        writer.close()

        with h5py.File(self.path, "r") as handle:
            columns, rows = read_string_table_dataset(handle["processed"]["roi_definitions"])
        self.assertEqual(columns[0:2], ["area_roi_id", "group_id"])
        self.assertEqual(rows[0][:2], ["1", "g1"])
        self.assertEqual(rows[1][:2], ["2", ""])


class AssignmentTableCompatibilityTests(_TempFileTestCase):
    def test_valve_state_labels_round_trip(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.write_valve_state_labels({"Open": "Loaded", "Close": "Waste"}, {"Open": "#123456"})
        writer.close()
        with h5py.File(self.path, "r") as handle:
            _columns, rows = read_string_table_dataset(
                handle["metadata"]["assignment_tables"]["valve_state_map"]
            )
        self.assertEqual(rows[0], ["Open", "Loaded", "#123456"])
        self.assertEqual(rows[1][0:2], ["Close", "Waste"])

    def test_color_palette_entries_skip_blank_colors_and_default_names(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.write_color_palette_entries([("", "#abcdef"), ("Named", ""), ("Blue", "#0000ff")])
        writer.close()
        with h5py.File(self.path, "r") as handle:
            _columns, rows = read_string_table_dataset(
                handle["metadata"]["assignment_tables"]["color_palette_entries"]
            )
        self.assertEqual(rows, [["Custom 1", "#ABCDEF"], ["Blue", "#0000FF"]])

    def test_switch_solution_labels_skip_blank_entries(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.write_switch_solution_labels(["Buffer A", "", "Buffer C"])
        writer.close()
        with h5py.File(self.path, "r") as handle:
            _columns, rows = read_string_table_dataset(
                handle["metadata"]["assignment_tables"]["switch_solution_map"]
            )
        self.assertEqual(rows, [["1", "Buffer A"], ["3", "Buffer C"]])


class ImageCubeManifestTests(_TempFileTestCase):
    def test_rows_accumulate_across_calls(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.append_image_cube_manifest_row(cube_index=0, timestamp_utc_ms=1000, file_path="C:/data/run1")
        writer.append_image_cube_manifest_row(cube_index=1, timestamp_utc_ms=2000, file_path="C:/data/run1")
        writer.close()
        with h5py.File(self.path, "r") as handle:
            _columns, rows = read_string_table_dataset(handle["metadata"]["image_cube_manifest"])
        self.assertEqual(rows, [["0", "1000", "C:/data/run1"], ["1", "2000", "C:/data/run1"]])


class SensorgramAndAbsorbanceTests(_TempFileTestCase):
    def test_sensorgram_points_grow_the_dataset(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.append_sensorgram_point(roi_id=1, timestamp_utc_ms=1000, metric_value=0.1)
        writer.append_sensorgram_point(roi_id=1, timestamp_utc_ms=2000, metric_value=0.2)
        writer.append_sensorgram_point(roi_id=2, timestamp_utc_ms=1000, metric_value=0.5)
        writer.close()

        with h5py.File(self.path, "r") as handle:
            roi1 = handle["processed"]["sensorgram"]["1"]
            roi2 = handle["processed"]["sensorgram"]["2"]
            np.testing.assert_array_equal(roi1["timestamp_utc_ms"][...], [1000, 2000])
            np.testing.assert_allclose(roi1["metric_value"][...], [0.1, 0.2])
            np.testing.assert_array_equal(roi2["timestamp_utc_ms"][...], [1000])

    def test_absorbance_spectra_grow_and_store_the_wavelength_axis_once(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        wavelengths = np.array([450.0, 500.0, 550.0])
        writer.append_absorbance_spectrum(
            roi_id=1, wavelengths_nm=wavelengths, absorbance=np.array([0.1, 0.2, 0.3]), cube_index=0
        )
        writer.append_absorbance_spectrum(
            roi_id=1, wavelengths_nm=wavelengths, absorbance=np.array([0.4, 0.5, 0.6]), cube_index=1
        )
        writer.close()

        with h5py.File(self.path, "r") as handle:
            group = handle["processed"]["absorbance_spectra"]["1"]
            np.testing.assert_array_equal(group["cube_index"][...], [0, 1])
            np.testing.assert_allclose(group["absorbance"][...], [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
            np.testing.assert_array_equal(group["wavelengths_nm"][...], wavelengths)


class ReadImagingSessionRoundTripTests(_TempFileTestCase):
    """2026-08-09: read_imaging_session() is the read-side counterpart that
    makes a v6.4 file usable as a real session file - these prove a full
    write-then-read cycle reconstructs equivalent settings/ROIs/assignment
    tables, not just that individual tables round-trip in isolation."""

    def test_empty_file_restores_to_empty_but_valid_snapshot(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        writer.close()

        snapshot = read_imaging_session(self.path)

        self.assertEqual(snapshot.settings.wavelengths_nm, [])
        self.assertEqual(snapshot.rois, [])
        self.assertFalse(snapshot.has_recorded_data)

    def test_full_setup_round_trips(self) -> None:
        writer = ImagingMeasurementWriter(self.path)
        settings = ImagingAcquisitionSettings(
            wavelengths_nm=[450.0, 500.0, 550.0],
            exposure_us=1000.0,
            gain=2.0,
            camera_settings_by_wavelength={
                500.0: WavelengthCameraSettings(exposure_us=9000.0, gain=3.0, binning=2, saving_mode="every_nth")
            },
            illumination_settings_by_wavelength={
                500.0: WavelengthIlluminationSettings(settle_time_ms=99.0, current=1.5, spectrum_source="measured")
            },
        )
        writer.write_illumination_settings(settings)
        writer.write_camera_settings(settings)
        rois = [
            AreaRoi(area_roi_id=1, center_x=10.0, center_y=20.0, sample_radius_px=5.0, sample_color_hex="#f59e0b"),
            AreaRoi(area_roi_id=2, center_x=30.0, center_y=40.0, sample_radius_px=6.0),
        ]
        groups = [AreaRoiGroup(group_id="g1", name="Pair 1", area_roi_ids=[1, 2])]
        writer.write_roi_definitions(rois, groups)
        writer.write_valve_state_labels({"Open": "Loaded", "Close": "Waste"}, {"Open": "#123456"})
        writer.write_color_palette_entries([("Blue", "#0000ff")])
        writer.write_switch_solution_labels(["Buffer A", "", "Buffer C"])
        writer.mark_recording_started()
        writer.close()

        snapshot = read_imaging_session(self.path)

        self.assertEqual(snapshot.settings.wavelengths_nm, [450.0, 500.0, 550.0])
        self.assertEqual(snapshot.settings.exposure_us, 1000.0)
        self.assertEqual(snapshot.settings.gain, 2.0)
        override = snapshot.settings.camera_settings_by_wavelength[500.0]
        self.assertEqual((override.exposure_us, override.gain, override.binning), (9000.0, 3.0, 2))
        illum_override = snapshot.settings.illumination_settings_by_wavelength[500.0]
        self.assertEqual((illum_override.settle_time_ms, illum_override.current), (99.0, 1.5))

        self.assertEqual([roi.area_roi_id for roi in snapshot.rois], [1, 2])
        self.assertEqual(snapshot.rois[0].sample_color_hex, "#f59e0b")
        self.assertEqual(len(snapshot.roi_groups), 1)
        self.assertEqual(snapshot.roi_groups[0].area_roi_ids, [1, 2])

        self.assertEqual(snapshot.valve_state_labels["Open"], "Loaded")
        self.assertEqual(snapshot.valve_state_colors["Open"], "#123456")
        self.assertEqual(snapshot.color_palette_entries, [("Blue", "#0000FF")])
        self.assertEqual(snapshot.switch_solution_labels, ["Buffer A", "", "Buffer C"])
        self.assertTrue(snapshot.has_recorded_data)


if __name__ == "__main__":
    unittest.main()
