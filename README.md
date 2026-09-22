# Brain Cell Counter v2.1.2

Brain Cell Counter is a Windows workflow for object-based fluorescent
brain-cell counting by Allen brain region. It coordinates ABBA/DeepSlice
registration, QuPath, Cellpose-SAM, regional classification, and report export.

Version 2 uses a PySide6/Qt interface with rounded controls, hover/pressed
feedback, soft page transitions, and automatic monitor sizing. Start the
previous compatibility interface with `python run_brain_cell_counter_v1.0.py
--classic` when using calibration or global-threshold tools during migration.

```text
ABBA -> DeepSlice Local -> QuPath project -> Brain Cell Counter
     -> QuPath Cellpose engine -> regional CSV and atlas overlay
```

## Quick start

On a configured computer, double-click
[`Start_Brain_Cell_Counter_v1.0.bat`](./Start_Brain_Cell_Counter_v1.0.bat).

Then:

1. Finish ABBA/DeepSlice registration and export regions to the QuPath project.
2. Use **IMPORT ABBA** if the exported atlas annotations are not yet loaded.
3. Close the QuPath desktop window to avoid simultaneous project access.
4. Select the project `project.qpproj` in Brain Cell Counter.
5. If stitched images lack trustworthy calibration, use **CALIBRATE** to add
   many images, review every proposed scale bar, and apply only
   human-confirmed values. Export verified Fiji-calibrated OME-TIFF copies for
   ABBA, or apply the calibration to backed-up QuPath metadata.
6. Run **PREFLIGHT**, then **VALIDATE**.
7. Review channel mapping, thresholds, and Cellpose parameters.
8. Select entries and run **ANALYZE**.

See [User guide](docs/USER_GUIDE.md) for the complete workflow.

## Install on another Windows computer

Use the generated installer package or build it from the installer source. The
installation is intentionally modular because the scientific dependencies are
large and are downloaded from their official pinned releases.

See [Installation guide](docs/INSTALLATION.md) and
[`installer/README_INSTALLER.md`](installer/README_INSTALLER.md).

## Documentation

- [Project file structure](docs/PROJECT_STRUCTURE.md)
- [Architecture and data flow](docs/ARCHITECTURE.md)
- [User guide](docs/USER_GUIDE.md)
- [Scientific method and validation](docs/SCIENTIFIC_METHOD.md)
- [Installation and dependency baseline](docs/INSTALLATION.md)
- [Third-party notices](installer/THIRD_PARTY_NOTICES.md)

## Main outputs

Each successfully analyzed image produces:

```text
<QuPath project>/brain_cell_counter_results/<image>_regional_counts_v2.0.csv
<QuPath project>/brain_cell_counter_results/<image>_atlas_overlay_v2.0.png
```

Batch progress is recorded in:

```text
<QuPath project>/brain_cell_counter_results/batch_checkpoint_v2.0.json
```

Human-reviewed calibration records and QC previews are written to:

```text
<QuPath project>/brain_cell_counter_calibration/calibration_manifest_v1.2.csv
<QuPath project>/brain_cell_counter_calibration/calibration_audit_v1.2.json
<QuPath project>/brain_cell_counter_calibration/qc/
```

The compact calibration manifest contains only essential image identity,
scale, status, and output fields. It includes `pixel_width_mm` and
`pixel_height_mm`. The accompanying JSON audit preserves all technical review
details for reproducibility.

## Scientific limitation

The output is object-based marker co-expression inside DAPI-defined objects.
It is not pixel-level colocalization. Thresholds and segmentation parameters
must be validated against controls, intensity distributions, and manual review.
See [Scientific method and validation](docs/SCIENTIFIC_METHOD.md).

## Versioning

The current application release is **v2.1.2**. Patch versions are increased
for fixes, minor versions for backward-compatible features, and major versions
for incompatible workflow changes. Some launcher and engine source filenames
retain `v1.0` for compatibility with existing shortcuts; the application
window, manifest, reports, and new analysis outputs carry the current version.

## Development

Run the Python test suite with:

```powershell
python -m unittest discover -s brain_cell_counter -p "test_*.py"
```

Repository-specific guidance for Codex and contributors is in
[`AGENTS.md`](AGENTS.md).
