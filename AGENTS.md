# Brain Cell Counter repository guidance

## Purpose

This repository contains a Windows application for object-based fluorescent
brain-cell counting by Allen brain region.

The scientific workflow is:

```text
ABBA -> DeepSlice Local -> QuPath project -> Python GUI
     -> QuPath Cellpose engine -> regional CSV and atlas overlay
```

## Read only what the task needs

- Start with this file and the root `README.md`.
- Read `docs/ARCHITECTURE.md` for component boundaries, data flow, and outputs.
- Read `docs/USER_GUIDE.md` for GUI behavior and the analysis workflow.
- Read `docs/SCIENTIFIC_METHOD.md` for threshold optimization, biological
  interpretation, and validation constraints.
- Read `docs/INSTALLATION.md` and `installer/README_INSTALLER.md` only for
  installation, packaging, dependency, or release work.
- Do not inspect generated copies under `dist/` or `installer/build/`; edit the
  source files instead.

## Sources of truth

- Python launcher: `run_brain_cell_counter_v1.0.py`
- Python GUI and orchestration: `brain_cell_counter/hybrid_app.py`
- Threshold algorithms and reports: `brain_cell_counter/threshold_optimizer.py`
- QuPath analysis engine: `Brain_Cell_Counter_QuPath_Engine_v1.0.groovy`
- Installer source: `installer/`
- Generated packages: `dist/` and `installer/build/` (never edit directly)

## Scientific invariants

- Counts represent object-based co-expression inside DAPI-defined objects, not
  pixel-level colocalization.
- Thresholds require validation against controls, intensity distributions, and
  manual inspection.
- Process QuPath project entries sequentially. Do not run concurrent Cellpose
  jobs against the same project.
- Preserve the Allen atlas hierarchy and the selected left/right hemisphere
  behavior.
- Keep Cellpose detections on the separate `__BCC_ANALYSIS_ROI__` parent so
  segmentation does not replace the atlas tree.

## Supported dependency baseline

- Windows 10/11, 64-bit
- Python 3.10
- QuPath 0.7.0
- Cellpose 4.2.1.1
- QuPath Cellpose extension 0.12.1
- ABBA 0.11.0
- QuPath ABBA extension 0.5.0
- Warpy extension 0.5.0

Do not upgrade pinned scientific dependencies unless the user explicitly asks.
If an upgrade is requested, verify compatibility and reproducibility before
changing installer pins or checksums.

## Verification

After changing Python code, run:

```powershell
python -m unittest discover -s brain_cell_counter -p "test_*.py"
```

After changing the QuPath engine, verify validation and analysis with a test
QuPath project when one is available. Never claim biological correctness from
automated tests alone.

After changing installer source, rebuild the package and verify its manifest,
download hashes, and installation checks. Do not commit generated build output.

## Repository hygiene

- After completing and verifying each requested code or documentation change,
  create a local Git commit with a concise descriptive message. Do not push or
  create a remote unless the user explicitly requests it.
- Never commit fluorescence source images, QuPath projects, generated result
  folders, local settings, credentials, or machine-specific absolute paths.
- `brain_cell_counter_settings.json` is local user state and must remain
  ignored. Use `brain_cell_counter_settings.example.json` for documentation.
- Keep documentation links relative so they work locally and on GitHub.
- Preserve unrelated user changes.
- Write source-code comments, module docstrings, function explanations, and
  installer-script comments in English.
- Prefer comments that explain intent, scientific assumptions, safety
  boundaries, and non-obvious control flow; do not restate trivial syntax.

## Release versioning

- Every user-visible fix or feature must increment the application version.
- Use semantic versioning: patch for fixes, minor for backward-compatible
  features, and major for incompatible workflow changes.
- Update `brain_cell_counter/version.py`, `CHANGELOG.md`, the installer
  manifest, displayed version, and affected versioned output schemas together.
- Preserve readers for older result files when practical.

## Code review rules

- Flag changes that can alter cell counts, region assignment, hemisphere
  aggregation, threshold selection, checkpoint signatures, or output columns.
- Flag any path that can write to a QuPath project without validation, backup,
  or explicit user intent.
- Flag concurrent processing that can write to the same QuPath project or
  compete for GPU memory.
- Treat generated-file edits as a build-process error; change their sources.
