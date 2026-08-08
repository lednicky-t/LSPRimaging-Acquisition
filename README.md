# LSPRimaging Acquisition

Desktop application for multispectral LSPR imaging acquisition: a tunable illumination
source (LCTF or LED array) and a camera sweep a configured wavelength list, assembling a
spectral cube per sweep, extracting per-ROI extinction spectra, and feeding a sensorgram
- continuously, for the duration of an experiment.

This app is built on `lspr_acq_shell`, the shared live-acquisition shell (fluidics device
framework, experiment-control runtime, sensorgram plot-cache engine, session/HDF5-writer
base, diagnostics) extracted from singleLSPR Acquisition. See
[`docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md`](../../../docs/architecture/general/lspri_acq_architecture_and_shared_shell_plan.md)
in the umbrella repo for the full design and delivery-milestones checklist, and
[`lspri_acq_build_log.md`](../../../docs/architecture/general/lspri_acq_build_log.md) for
the dated build history.

**Status: early scaffold, under active development.** v1 targets SW-triggered acquisition
only, a Basler camera, and manual ROI placement - see the plan doc's "Goals and non-goals"
section for the full v1 scope.

## Repository layout

- `src/lspri_acq_app/` - application package
  - `device/` - `Camera`/`IlluminationSource` device abstraction + simulated backends
  - `domain/` - typed data models (`Frame`, `SpectralCube`, ROI types)
  - `gui/` - Qt windows and panels
- `tests/` - app-specific tests (device/domain logic; no Qt, no hardware required)

## Quick start

1. Create and activate a virtual environment.
2. Install the project in editable mode:

```powershell
python -m pip install -e .
```

3. Run the application:

```powershell
lspri-acquisition
```

If you prefer the package entry point:

```powershell
python -m lspri_acq_app.app
```

## Notes

- Depends on `lspr-core`, `lspr-io`, `lspr-ui`, and `lspr-acq-shell` from the umbrella
  LSPR Suite workspace - install via that repo's `requirements.txt` for local development.
- Basler camera support requires `pypylon`. IDS uEye support (evaluated as an alternative
  camera, see the architecture plan) requires the separately-installed IDS Software Suite
  driver and is not wired into this app yet.
