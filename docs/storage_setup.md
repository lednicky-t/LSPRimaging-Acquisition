# Image storage setup — TIFF stack and OME-Zarr

This app stores captured frames as either a TIFF stack or an OME-Zarr dataset —
both are supported, user-selectable options (see `storage/image_writer.py`'s
`StorageSettings`), not a single fixed choice. Experimental hardware and setups
vary machine to machine, camera to camera; what performs well on one rig may not
on another. Measured starting points are below — verify them on your own setup
using the live save-lag metrics (`SaveWriterThread.stats()`), don't assume they
transfer.

Full measurement methodology and numbers:
[`spikes/lspri_acq_storage_benchmark/storage_format_benchmark_findings.md`](../../../spikes/lspri_acq_storage_benchmark/storage_format_benchmark_findings.md)
(paths relative to the umbrella repo).

## Exclude the save destination from antivirus real-time scanning

Both formats write many small files as an experiment runs (one TIFF per frame,
or one zarr shard file per cube/wavelength depending on `shard_mode`). LSPRimaging
Evaluation's own OME-Zarr exporter hit a real, documented problem from this on
Windows: antivirus real-time scanning and network-drive latency made naive
many-small-files layouts slow enough to matter (see that app's `TODO.md`) — fixed
there by moving to shard-based grouping, which this app's `OmeZarrCubeWriter`
already uses by default (`shard_mode="per_spectral_cube"`, one file per cube, not
per wavelength). The same class of problem can still happen if:

- The save destination is on a network drive rather than local disk.
- Antivirus real-time protection is scanning every new file as it's written.

**Add the experiment's save destination folder to your antivirus's exclusion
list** (Windows Security → Virus & threat protection → Manage settings →
Exclusions → Add an exclusion → Folder) before a real acquisition run, and prefer
a local disk over a network share for the live write destination — copy to
network storage afterward if needed, not during acquisition.

## Shard mode (OME-Zarr only)

- `shard_mode="per_spectral_cube"` (the default): one shard file per completed
  cube, covering all its wavelengths. Produces the fewest files for a live,
  open-ended-length acquisition — the right default for *writing* during an
  experiment.
- `shard_mode="per_image"`: one shard file per (cube, wavelength) pair — more
  files, but lets a reader fetch a single wavelength plane without touching the
  rest of its cube. This is eva's own default for its *batch exporter*, tuned
  for browsing/analysis access patterns, not live writing — not the right
  default here, though still a supported option.

## Compression

See the benchmark findings doc for real numbers, but the short version: `lz4` is
fast enough to keep up live at 2×2 binning, but was measured *borderline* against
the sweep's own pace at full resolution on a single save thread — the exact
scenario `SaveWriterThread.stats()` (queue depth, write latency, bytes written)
exists to let you watch for in real time, on your own hardware, rather than
trusting a number measured on a different machine.

If full-resolution live compression turns out not to keep up on your setup: write
uncompressed live (`compression="none"`, always comfortably fast at every tested
resolution) and recompress afterward — eva's existing batch OME-Zarr exporter
(`export_ome_zarr_dataset`, `ProcessPoolExecutor`-parallelized across every CPU
core) already does exactly this well, just applied after acquisition finishes
rather than during it.
