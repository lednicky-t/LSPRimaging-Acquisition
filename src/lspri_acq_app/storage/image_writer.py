"""Image cube writers: TIFF stack and OME-Zarr, both live/incremental.

Both formats are kept as user-selectable options, not one chosen for the
user - this is an experimental app; the maintainer's explicit call is that
customization and flexibility matter more than a single hardcoded default.
See spikes/lspri_acq_storage_benchmark/storage_format_benchmark_findings.md
for the measurements informing sensible starting values (StorageSettings'
defaults below), not hard rules - every field there is meant to be
overridden by the user, later, through a real settings UI (not built yet).

TIFF writer: filename convention (WL<wavelength>Frame<cube_index>.tif)
matches LSPRimaging Evaluation's existing reader (IMAGE_PATTERN in
apps/LSPRi/eva/src/lspr_imaging_app/io/dataset.py) exactly - no eva-side
change needed to read what this writes.

OME-Zarr writer: grows a real zarr v3 array one cube at a time (using
zarr's own API for array/metadata structure, so the result is a standard,
valid zarr v3 dataset any zarr-v3-compliant reader can open), but writes
each cube's pixel data with the same hand-rolled shard/index/CRC32C format
eva's batch exporter uses (_zarr_export_worker.py's write_shard()) -
bypassing zarr's own async per-chunk write path, the thing that made that
exporter fast (119 MB/s vs 21 MB/s at a 64px chunk size, per that module's
own docstring). Also writes the same "lspr" attrs group
export_ome_zarr_dataset() does, updated after every cube, so eva's
fast-read path (_ome_zarr_fast_read_metadata) works against this too, and a
dataset from an interrupted experiment stays readable for whatever cubes
did complete.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import tifffile

from lspri_acq_app.domain.models import SpectralCube


def _crc32c(data: bytes) -> int:
    """CRC32C (Castagnoli) checksum, required by the zarr v3 shard index
    format - same fallback chain as
    apps/LSPRi/eva/src/lspr_imaging_app/io/_zarr_export_worker.py's
    _crc32c(): the real `crc32c` package if installed, else a pure-Python
    table-based implementation (the shard index is at most a few KB, fast
    enough either way)."""
    try:
        import crc32c as _lib  # type: ignore[import-untyped]

        return _lib.crc32c(data)
    except ImportError:
        pass
    poly = 0x82F63B78
    table: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    crc = 0xFFFFFFFF
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


@dataclass(slots=True)
class StorageSettings:
    """Every field here is meant to be user-choosable via a future settings
    UI - defaults are the storage-format-benchmark's suggested starting
    points (comfortably fast at 2x2 binning; uncompressed is the safe
    choice at full resolution until a real recompress-after-acquisition
    pass exists), not fixed policy.
    """

    format: str = "ome_zarr"  # "tiff" | "ome_zarr"
    compression: str = "none"  # "none" | "lz4" | "zstd"
    compression_level: int = 1
    shard_mode: str = "per_spectral_cube"  # "per_spectral_cube" | "per_image" (ome_zarr only)
    chunk_size_px: int = 64  # ome_zarr only


class ImageCubeWriter(Protocol):
    def write_cube(self, cube: SpectralCube) -> int:
        """Write one cube; return the number of raw (uncompressed) bytes written."""
        ...

    def close(self) -> None: ...


class TiffCubeWriter:
    """One TIFF file per frame: WL<wavelength>Frame<cube_index>.tif."""

    def __init__(self, destination: Path, *, compression: str = "none") -> None:
        if compression not in {"none", "zlib"}:
            raise ValueError(f"TiffCubeWriter supports compression 'none'/'zlib', got {compression!r}")
        self._destination = Path(destination)
        self._destination.mkdir(parents=True, exist_ok=True)
        self._compression = None if compression == "none" else compression

    def write_cube(self, cube: SpectralCube) -> int:
        total_bytes = 0
        for frame in cube.frames:
            filename = f"WL{frame.wavelength_nm:g}Frame{cube.cube_index}.tif"
            tifffile.imwrite(self._destination / filename, frame.image, compression=self._compression)
            total_bytes += frame.image.nbytes
        return total_bytes

    def close(self) -> None:
        pass


_ZARR_CODEC_NAMES = {"none": None, "lz4": "lz4", "zstd": "zstd"}


class OmeZarrCubeWriter:
    """Grows a real zarr v3 array one SpectralCube at a time.

    Requires the wavelength list up front (ImagingAcquisitionSettings
    already has it before a sweep starts, per the maintainer's confirmation
    that this is never an unknown) - the array's per-cube shape is fixed
    from that, only the cube-count axis grows.
    """

    def __init__(
        self,
        destination: Path,
        *,
        wavelengths_nm: list[float],
        image_shape: tuple[int, int],
        dtype: np.dtype | str = np.dtype("uint16"),
        compression: str = "none",
        compression_level: int = 1,
        shard_mode: str = "per_spectral_cube",
        chunk_size_px: int = 64,
    ) -> None:
        import zarr
        from zarr.codecs import BloscCodec
        from zarr.storage import LocalStore

        if shard_mode not in {"per_spectral_cube", "per_image"}:
            raise ValueError(f"Unknown shard_mode {shard_mode!r}")
        if compression not in _ZARR_CODEC_NAMES:
            raise ValueError(f"Unknown compression {compression!r}")
        codec_name = _ZARR_CODEC_NAMES[compression]

        self._destination = Path(destination)
        if self._destination.suffix != ".zarr" and not self._destination.name.endswith(".ome.zarr"):
            self._destination = self._destination.with_suffix(".ome.zarr")
        self._wavelengths_nm = list(wavelengths_nm)
        self._n_wl = len(self._wavelengths_nm)
        if self._n_wl == 0:
            raise ValueError("wavelengths_nm must be non-empty")
        self._height, self._width = image_shape
        self._dtype = np.dtype(dtype)
        self._compression = compression
        self._compression_cname = codec_name
        self._shard_mode = shard_mode
        self._chunk_size_px = int(chunk_size_px)

        from numcodecs import Blosc as _NumcodecsBlosc

        self._codec = (
            _NumcodecsBlosc(cname=codec_name, clevel=compression_level, shuffle=_NumcodecsBlosc.BITSHUFFLE)
            if codec_name is not None
            else None
        )

        ich = icw = self._chunk_size_px
        self._sh = ((self._height + ich - 1) // ich) * ich
        self._sw = ((self._width + icw - 1) // icw) * icw
        self._n_cy = self._sh // ich
        self._n_cx = self._sw // icw

        self._destination.mkdir(parents=True, exist_ok=True)
        store = LocalStore(str(self._destination))
        self._group = zarr.open_group(store=store, mode="w", zarr_format=3)
        self._array = self._group.create_array(
            "0",
            shape=(0, self._n_wl, self._height, self._width),
            chunks=(1, 1, ich, icw),
            shards=(1, self._n_wl, self._sh, self._sw) if shard_mode == "per_spectral_cube" else (1, 1, self._sh, self._sw),
            dtype=self._dtype,
            fill_value=0 if np.issubdtype(self._dtype, np.integer) else np.nan,
            compressors=(
                BloscCodec(cname=codec_name, clevel=compression_level, shuffle="bitshuffle") if codec_name else None
            ),
        )
        self._shard_base_dir = self._destination / "0" / "c"
        self._spectral_cube_indices: list[int] = []
        self._write_metadata()

    def write_cube(self, cube: SpectralCube) -> int:
        if len(cube.frames) != self._n_wl:
            raise ValueError(
                f"Cube {cube.cube_index} has {len(cube.frames)} frames, expected {self._n_wl} "
                "(this writer was configured for a fixed wavelength list)."
            )
        position = len(self._spectral_cube_indices)
        self._array.resize((position + 1, self._n_wl, self._height, self._width))

        if self._shard_mode == "per_spectral_cube":
            total_bytes = self._write_shard(
                shard_path=self._shard_base_dir / str(position) / "0" / "0" / "0",
                planes=[frame.image for frame in cube.frames],
                n_planes=self._n_wl,
            )
        else:
            total_bytes = 0
            for wl_index, frame in enumerate(cube.frames):
                total_bytes += self._write_shard(
                    shard_path=self._shard_base_dir / str(position) / str(wl_index) / "0" / "0",
                    planes=[frame.image],
                    n_planes=1,
                )

        self._spectral_cube_indices.append(cube.cube_index)
        self._write_metadata()
        return total_bytes

    def _write_shard(self, *, shard_path: Path, planes: list[np.ndarray], n_planes: int) -> int:
        n_inner = n_planes * self._n_cy * self._n_cx
        bufs: list[bytes] = []
        offsets = np.full(n_inner, np.uint64((1 << 64) - 1), dtype=np.uint64)
        lengths = np.full(n_inner, np.uint64((1 << 64) - 1), dtype=np.uint64)
        byte_offset = 0
        total_bytes = 0

        for plane_index, image in enumerate(planes):
            total_bytes += image.nbytes
            plane = self._pad_plane(image)
            base_ci = plane_index * (self._n_cy * self._n_cx)
            for cy in range(self._n_cy):
                for cx in range(self._n_cx):
                    tile = np.ascontiguousarray(
                        plane[
                            cy * self._chunk_size_px : (cy + 1) * self._chunk_size_px,
                            cx * self._chunk_size_px : (cx + 1) * self._chunk_size_px,
                        ]
                    )
                    encoded = self._codec.encode(tile.tobytes()) if self._codec is not None else tile.tobytes()
                    ci = base_ci + cy * self._n_cx + cx
                    offsets[ci] = byte_offset
                    lengths[ci] = len(encoded)
                    bufs.append(encoded)
                    byte_offset += len(encoded)

        index = np.empty(n_inner * 2, dtype=np.uint64)
        index[0::2] = offsets
        index[1::2] = lengths
        index_bytes = index.tobytes()

        shard_path.parent.mkdir(parents=True, exist_ok=True)
        with shard_path.open("wb") as f:
            for buf in bufs:
                f.write(buf)
            f.write(index_bytes)
            f.write(struct.pack("<I", _crc32c(index_bytes)))
        return total_bytes

    def _pad_plane(self, image: np.ndarray) -> np.ndarray:
        if image.shape != (self._sh, self._sw):
            padded = np.zeros((self._sh, self._sw), dtype=self._dtype)
            padded[: self._height, : self._width] = image
            return padded
        return np.ascontiguousarray(image, dtype=self._dtype)

    def _write_metadata(self) -> None:
        self._group.attrs["lspr"] = {
            "spectral_cube_indices": list(self._spectral_cube_indices),
            "wavelengths_nm": list(self._wavelengths_nm),
            "source": "live_acquisition",
            "chunk_size_px": self._chunk_size_px,
            "shard_mode": self._shard_mode,
            "compression": f"{self._compression_cname}+bitshuffle" if self._compression_cname else "none",
            "dtype": str(self._dtype),
        }

    def close(self) -> None:
        pass


def build_image_writer(
    settings: StorageSettings,
    destination: Path,
    *,
    wavelengths_nm: list[float],
    image_shape: tuple[int, int],
    dtype: np.dtype | str = np.dtype("uint16"),
) -> ImageCubeWriter:
    if settings.format == "tiff":
        return TiffCubeWriter(destination, compression="zlib" if settings.compression != "none" else "none")
    if settings.format == "ome_zarr":
        return OmeZarrCubeWriter(
            destination,
            wavelengths_nm=wavelengths_nm,
            image_shape=image_shape,
            dtype=dtype,
            compression=settings.compression,
            compression_level=settings.compression_level,
            shard_mode=settings.shard_mode,
            chunk_size_px=settings.chunk_size_px,
        )
    raise ValueError(f"Unknown storage format {settings.format!r}")
