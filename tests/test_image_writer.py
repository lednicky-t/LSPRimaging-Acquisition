"""Tests for storage/image_writer.py - both writers verified by actually
reading back what they wrote (tifffile.imread / zarr's own generic array
API), not just checking that write_cube() didn't raise.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import tifffile

from lspri_acq_app.domain.models import Frame, SpectralCube
from lspri_acq_app.storage.image_writer import (
    OmeZarrCubeWriter,
    StorageSettings,
    TiffCubeWriter,
    build_image_writer,
)


def _make_cube(cube_index: int, wavelengths_nm: list[float], height: int = 32, width: int = 40) -> SpectralCube:
    now = datetime.now(timezone.utc)
    frames = [
        Frame(
            image=np.full((height, width), fill_value=(cube_index * 100 + i), dtype=np.uint16),
            wavelength_nm=wavelength,
            acquired_at=now,
        )
        for i, wavelength in enumerate(wavelengths_nm)
    ]
    return SpectralCube(frames=frames, cube_index=cube_index, started_at=now, completed_at=now)


class TiffCubeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.destination = Path(self._tmp.name)

    def test_filenames_match_evas_image_pattern_convention(self) -> None:
        writer = TiffCubeWriter(self.destination)
        writer.write_cube(_make_cube(3, [450.0, 500.5]))

        self.assertTrue((self.destination / "WL450Frame3.tif").exists())
        self.assertTrue((self.destination / "WL500.5Frame3.tif").exists())

    def test_round_trip_pixel_values_are_lossless_uncompressed(self) -> None:
        writer = TiffCubeWriter(self.destination, compression="none")
        cube = _make_cube(1, [450.0, 500.0])
        writer.write_cube(cube)

        for frame in cube.frames:
            path = self.destination / f"WL{frame.wavelength_nm:g}Frame1.tif"
            read_back = tifffile.imread(path)
            np.testing.assert_array_equal(read_back, frame.image)

    def test_round_trip_pixel_values_are_lossless_zlib_compressed(self) -> None:
        writer = TiffCubeWriter(self.destination, compression="zlib")
        cube = _make_cube(2, [450.0])
        writer.write_cube(cube)

        read_back = tifffile.imread(self.destination / "WL450Frame2.tif")
        np.testing.assert_array_equal(read_back, cube.frames[0].image)

    def test_returns_raw_byte_count(self) -> None:
        writer = TiffCubeWriter(self.destination)
        cube = _make_cube(1, [450.0, 500.0])
        written = writer.write_cube(cube)
        self.assertEqual(written, sum(f.image.nbytes for f in cube.frames))

    def test_rejects_unknown_compression(self) -> None:
        with self.assertRaises(ValueError):
            TiffCubeWriter(self.destination, compression="lz4")


class OmeZarrCubeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.destination = Path(self._tmp.name) / "dataset"

    def _open_array(self, destination: Path):
        import zarr

        group = zarr.open_group(store=str(destination), mode="r")
        return group, group["0"]

    def test_creates_a_valid_zarr_v3_array_with_expected_shape(self) -> None:
        writer = OmeZarrCubeWriter(
            self.destination, wavelengths_nm=[450.0, 500.0], image_shape=(32, 40), compression="none"
        )
        writer.write_cube(_make_cube(0, [450.0, 500.0], height=32, width=40))

        _group, array = self._open_array(writer._destination)
        self.assertEqual(array.shape, (1, 2, 32, 40))

    def test_round_trip_pixel_values_uncompressed(self) -> None:
        writer = OmeZarrCubeWriter(
            self.destination, wavelengths_nm=[450.0, 500.0], image_shape=(32, 40), compression="none"
        )
        cube = _make_cube(0, [450.0, 500.0], height=32, width=40)
        writer.write_cube(cube)

        _group, array = self._open_array(writer._destination)
        for wl_index, frame in enumerate(cube.frames):
            np.testing.assert_array_equal(array[0, wl_index], frame.image)

    def test_round_trip_pixel_values_lz4_compressed(self) -> None:
        writer = OmeZarrCubeWriter(
            self.destination,
            wavelengths_nm=[450.0, 500.0],
            image_shape=(32, 40),
            compression="lz4",
        )
        cube = _make_cube(0, [450.0, 500.0], height=32, width=40)
        writer.write_cube(cube)

        _group, array = self._open_array(writer._destination)
        for wl_index, frame in enumerate(cube.frames):
            np.testing.assert_array_equal(array[0, wl_index], frame.image)

    def test_round_trip_pixel_values_per_image_shard_mode(self) -> None:
        writer = OmeZarrCubeWriter(
            self.destination,
            wavelengths_nm=[450.0, 500.0],
            image_shape=(32, 40),
            compression="lz4",
            shard_mode="per_image",
        )
        cube = _make_cube(0, [450.0, 500.0], height=32, width=40)
        writer.write_cube(cube)

        _group, array = self._open_array(writer._destination)
        for wl_index, frame in enumerate(cube.frames):
            np.testing.assert_array_equal(array[0, wl_index], frame.image)

    def test_sequential_cubes_append_at_the_right_position(self) -> None:
        writer = OmeZarrCubeWriter(self.destination, wavelengths_nm=[450.0], image_shape=(32, 40), compression="none")
        first = _make_cube(0, [450.0], height=32, width=40)
        second = _make_cube(1, [450.0], height=32, width=40)
        writer.write_cube(first)
        writer.write_cube(second)

        _group, array = self._open_array(writer._destination)
        self.assertEqual(array.shape, (2, 1, 32, 40))
        np.testing.assert_array_equal(array[0, 0], first.frames[0].image)
        np.testing.assert_array_equal(array[1, 0], second.frames[0].image)

    def test_lspr_attrs_track_written_cube_indices_and_wavelengths(self) -> None:
        writer = OmeZarrCubeWriter(
            self.destination, wavelengths_nm=[450.0, 500.0], image_shape=(32, 40), compression="lz4"
        )
        writer.write_cube(_make_cube(5, [450.0, 500.0], height=32, width=40))
        writer.write_cube(_make_cube(7, [450.0, 500.0], height=32, width=40))

        group, _array = self._open_array(writer._destination)
        attrs = group.attrs["lspr"]
        self.assertEqual(attrs["spectral_cube_indices"], [5, 7])
        self.assertEqual(attrs["wavelengths_nm"], [450.0, 500.0])
        self.assertEqual(attrs["shard_mode"], "per_spectral_cube")
        self.assertEqual(attrs["compression"], "lz4+bitshuffle")

    def test_wrong_frame_count_raises(self) -> None:
        writer = OmeZarrCubeWriter(
            self.destination, wavelengths_nm=[450.0, 500.0], image_shape=(32, 40), compression="none"
        )
        wrong_cube = _make_cube(0, [450.0], height=32, width=40)
        with self.assertRaises(ValueError):
            writer.write_cube(wrong_cube)

    def test_returns_raw_byte_count(self) -> None:
        writer = OmeZarrCubeWriter(
            self.destination, wavelengths_nm=[450.0, 500.0], image_shape=(32, 40), compression="lz4"
        )
        cube = _make_cube(0, [450.0, 500.0], height=32, width=40)
        written = writer.write_cube(cube)
        self.assertEqual(written, sum(f.image.nbytes for f in cube.frames))

    def test_rejects_unknown_shard_mode(self) -> None:
        with self.assertRaises(ValueError):
            OmeZarrCubeWriter(
                self.destination, wavelengths_nm=[450.0], image_shape=(32, 40), shard_mode="bogus"
            )

    def test_rejects_unknown_compression(self) -> None:
        with self.assertRaises(ValueError):
            OmeZarrCubeWriter(
                self.destination, wavelengths_nm=[450.0], image_shape=(32, 40), compression="bogus"
            )


class BuildImageWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.destination = Path(self._tmp.name)

    def test_builds_tiff_writer(self) -> None:
        writer = build_image_writer(
            StorageSettings(format="tiff"), self.destination, wavelengths_nm=[450.0], image_shape=(32, 40)
        )
        self.assertIsInstance(writer, TiffCubeWriter)

    def test_builds_ome_zarr_writer(self) -> None:
        writer = build_image_writer(
            StorageSettings(format="ome_zarr"), self.destination / "d", wavelengths_nm=[450.0], image_shape=(32, 40)
        )
        self.assertIsInstance(writer, OmeZarrCubeWriter)

    def test_unknown_format_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_image_writer(
                StorageSettings(format="bogus"), self.destination, wavelengths_nm=[450.0], image_shape=(32, 40)
            )


if __name__ == "__main__":
    unittest.main()
