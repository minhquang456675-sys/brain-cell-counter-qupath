# Changelog

All user-visible releases follow semantic versioning.

## 2.5.1 — 2026-09-23

### Fixed

- Replaced the native Windows scrollbars in the main and Calibration Qt
  interfaces with compact rounded tracks, hover feedback, and no arrow buttons.

## 2.5.0 — 2026-09-23

### Changed

- Regional output schema 2.1 replaces the ambiguous `Triple_Positive` label
  with the explicit channel-derived `DAPI+cFOS+NeuN` count and matching
  density/percentage names.

### Added

- Regional CSVs now include a separate `cFOS+NeuN` object-based
  double-positive count and density. With no DAPI intensity requirement it is
  expected to equal `DAPI+cFOS+NeuN`; with the DAPI threshold enabled it may be
  higher because the triple-positive count remains DAPI-qualified.

## 2.4.1 — 2026-09-23

### Fixed

- Calibration now restores complete records from
  `calibration_audit_v1.2.json` and automatically lists all accessible images
  in the selected QuPath project when the window opens.
- Previously confirmed, applied, and Fiji-verified states are visible across
  sessions instead of returning to an empty table.

## 2.4.0 — 2026-09-23

### Changed

- Replaced the default legacy Tk Calibration window with a native PySide6
  workspace using the same flat controls, rounded cards, typography, spacing,
  palette, table behavior, and responsive sizing as the main application.
- Rebuilt scale-bar review as a native Qt dialog with click-to-redraw
  endpoints, live physical-pixel-size feedback, and the existing manual
  confirmation and metadata-conflict safeguards.
- Disabled Calibration actions now have an unambiguous muted appearance, and
  the window fits inside the available Windows desktop area.

## 2.3.0 — 2026-09-23

### Added

- Calibration now uses a dedicated ChatGPT-inspired light workspace with a
  three-step workflow, live batch metrics, clearer activity feedback, and a
  redesigned scale-bar review window.
- Calibration actions are enabled only when the batch state supports them;
  status-colored rows distinguish confirmed, review-required, and failed
  records, and the table supports both vertical and horizontal scrolling.
- Calibration and review windows automatically fit and center on the active
  monitor while retaining the existing human-confirmation safeguards.

## 2.2.2 — 2026-09-23

### Fixed

- Calibration now opens as an independent visible window when launched from
  the Qt interface instead of inheriting a hidden Tk owner on Windows.
- Repeated clicks can no longer create multiple hidden Calibration processes;
  both the Qt interface and the standalone runner enforce a single window.

## 2.2.1 — 2026-09-23

### Fixed

- Removed the opacity page-transition effect that could leave complex Qt pages
  blank or visually disappear after a sidebar click on Windows.
- Results now loads lazily when the page is opened, avoids nested scroll areas,
  uses fixed bounded table widths instead of measuring every cell, and limits
  the on-screen preview while preserving full-row compact CSV export.
- Unexpected Qt callback errors now create a visible diagnostic message and a
  local runtime log instead of silently losing a screen.

### Added

- Results includes a bounded preview of the exact version-matched atlas overlay
  alongside the regional table.

## 2.2.0 — 2026-09-22

### Added

- Global threshold optimization is now integrated into the Qt Thresholds page,
  including channel-pair objectives, candidate ranges, plateau rules,
  background separation, report generation, and threshold locking.
- The Qt Results page now previews complete regional CSV tables, provides
  measurement presets and column selection, exports compact CSV files, and
  opens the exact saved image entry in QuPath.
- The Qt workflow now includes native project-link repair and non-saving
  Cellpose parameter suggestion.

### Changed

- Calibration opens its focused human-review wizard directly instead of
  launching the complete classic application window.
- The default launcher no longer silently falls back to the classic interface
  when Qt is unavailable; it reports the missing dependency as a startup error.

## 2.1.3 — 2026-09-22

### Fixed

- The source-tree launcher and development shortcuts now start the application
  directly from the real repository path under `Documents`, avoiding the
  virtualized LocalAppData view used when Codex runs as a packaged Windows app.
- The source launcher now prefers the Cellpose environment's `pythonw.exe`,
  falls back to the system Python GUI interpreter, and reports missing paths in
  a visible console instead of failing silently.

## 2.1.2 — 2026-09-22

### Fixed

- The Qt window is now restored, temporarily raised above other windows, and
  activated immediately and again after startup so shortcut launches do not
  remain hidden behind Explorer, Codex, or QuPath.
- Startup exceptions from `pythonw.exe` now create
  `brain_cell_counter_startup_error.log` and display a visible Windows error
  dialog instead of failing silently.

## 2.1.1 — 2026-09-22

### Fixed

- Desktop and installation-folder shortcuts now target the stable Cellpose
  `pythonw.exe` plus the installed application script directly. This avoids
  shortcut targets being redirected into the Codex application cache.
- The core and complete installers now create the same direct shortcut in both
  the Desktop and application installation folder.

## 2.1.0 — 2026-09-22

### Changed

- Grouped every QuPath and Fiji Groovy script under `qupath_scripts/` and
  updated both desktop interfaces, tests, documentation, and installer staging
  to resolve the categorized paths.
- Added a project-structure guide that distinguishes application source,
  scientific scripts, installer source, documentation, launchers, and
  generated or machine-specific files.
- Kept scientific output schema 2.0 because this release changes file
  organization only; cell counting, thresholds, region assignment, and output
  columns are unchanged.

## 2.0.0 — 2026-09-22

### Added

- Introduced a PySide6/Qt interface with rounded cards and controls, hover and
  pressed states, soft page transitions, subtle elevation, responsive monitor
  sizing, a fixed workflow sidebar, and a separate live run-log window.
- Added a toolkit-independent project and QuPath-command layer so interface
  changes cannot silently alter the arguments sent to the scientific engine.
- Added native Qt sequential validation, analysis, and ABBA-import queues.

### Changed

- The source launcher now opens the Qt interface by default and accepts
  `--classic` to open the previous Tk interface for advanced workflows that
  are still being migrated, including calibration and global thresholds.
- Added pinned PySide6 6.11.2 to the Windows runtime and installer checks.
- Versioned new analysis outputs and checkpoints with schema 2.0.

## 1.10.0 — 2026-09-22

### Changed

- Updated the interface to a ChatGPT-inspired light palette: white workspace,
  subtle gray surfaces, graphite text, dark primary buttons, and restrained
  green accents for progress, help cues, and successful states.
- Preserved the compact layout, status badges, advanced dialogs, keyboard
  shortcuts, and all scientific processing behavior from v1.9.1.

## 1.9.1 — 2026-09-22

### Changed

- Restored the clean white application background and white header preferred
  in the earlier interface.
- Retained blue only for primary actions, selected navigation, help links, and
  status emphasis while preserving all v1.9.0 usability improvements.

## 1.9.0 — 2026-09-22

### Changed

- Introduced a consistent navy, blue, white, and neutral application theme
  with clearer cards, tabs, primary actions, warnings, and disabled states.
- Added a live READY, RUNNING, COMPLETE, ERROR, or CANCELLED status badge beside
  the progress bar.
- Reduced the compact-layout height so the complete Start workflow and footer
  fit within the detected 804-pixel desktop workspace.
- Added Ctrl+O for project selection, Ctrl+L for the run log, and F1 for Help.
- Advanced Cellpose and plateau windows are now single-instance and are closed
  automatically before a project operation starts.

## 1.8.1 — 2026-09-22

### Fixed

- Advanced Cellpose settings now open in a dedicated visible window instead of
  expanding below the clipped tab viewport.
- Advanced plateau settings use the same dialog behavior so every field remains
  accessible on compact displays.

## 1.8.0 — 2026-09-22

### Changed

- Added an always-visible current-project context bar with the selected entry,
  project entry count, and ABBA readiness status.
- Simplified navigation labels to Start, Analysis, Thresholds, and Results.
- Reorganized Analysis into channel names, intensity thresholds, analysis
  behavior, and a collapsed advanced Cellpose section.
- Reduced Thresholds to its common controls and moved search ranges and
  plateau criteria into a collapsed advanced section.
- Moved the complete Results column checklist into a separate scrollable
  chooser to prevent long measurement names from widening the main window.
- Preserved all existing values and processing behavior when advanced sections
  are hidden.

## 1.7.0 — 2026-09-22

### Changed

- Replaced the verbose calibration CSV with a compact 16-column results table
  containing only image identity, calibration inputs, pixel dimensions,
  confirmation/application status, output path, and notes.
- Moved the complete technical calibration state to
  `calibration_audit_v1.2.json` for reproducibility without cluttering the CSV.
- New compact calibration files use `calibration_manifest_v1.2.csv`; existing
  calibration manifests are preserved.

## 1.6.1 — 2026-09-22

### Added

- Calibration manifest CSV files now include `pixel_width_mm` and
  `pixel_height_mm`.
- Verified Fiji pixel dimensions take precedence; otherwise the columns use
  the human-confirmed isotropic calibration proposal.
- The updated calibration schema is written to `calibration_manifest_v1.1.csv`
  so an existing v1.0 manifest is preserved.

## 1.6.0 — 2026-09-22

### Added

- **EXPORT CALIBRATED WITH FIJI** for human-confirmed scale-bar records.
- Sequential virtual-stack OME-TIFF export with automatic reopen and JSON
  verification of geometry, channels, bit depth, and physical pixel size.
- Fiji launcher path in Settings and persistent local configuration.

### Safety

- Source images and existing calibrated exports are never overwritten.
- Fiji export does not require or modify a QuPath project entry.

## 1.5.0 — 2026-09-22

### Added

- **Essential** result preset containing core identifiers, hemisphere, area,
  and cell counts.
- **EXPORT VISIBLE CSV** writes every result row with exactly the currently
  selected columns.

### Safety

- Compact export creates a separate `_selected.csv`; the complete versioned
  master result is never modified.

## 1.4.0 — 2026-09-22

### Changed

- Replaced the custom cell-overlay viewer with **OPEN IN QUPATH** in Results
  Preview.
- The selected regional CSV now opens its exact saved project image entry in
  the normal QuPath desktop viewer.
- Removed the preview-coordinate point CSV export; regional CSV and atlas PNG
  exports remain available.

### Safety

- QuPath launch is disabled while validation, analysis, ABBA import, or
  calibration is active, preventing concurrent access to the same project.

## 1.3.0 — 2026-09-22

### Added

- QuPath-style cell-overlay workspace with a dark image canvas and right-side
  detection-layer panel.
- Mouse-wheel zoom at the cursor, left-drag panning, Fit, 100%, zoom buttons,
  image coordinates, and layer-specific object totals.
- New analysis outputs and checkpoints use the v1.3 schema suffix. Existing
  v1.2 regional CSV and overlay pairs remain viewable.

### Safety

- The viewer remains read-only and does not open the QuPath project for
  writing, so layer inspection cannot conflict with batch analysis.

## 1.2.2 — 2026-09-22

### Fixed

- Moved the atlas preview coordinate transform into script scope so the
  interactive cell-overlay point export can reuse it after atlas rendering.
- Prevented `MissingPropertyException: atlasScaleX` near the end of ANALYZE.

## 1.2.1 — 2026-09-22

### Changed

- Added English module documentation, function explanations, script headers,
  and installer comments across all maintained source-code files.
- No scientific algorithm, output schema, threshold, or classification logic
  changed in this patch release.

## 1.2.0 — 2026-09-22

### Added

- Interactive cell-overlay viewer opened from Results Preview.
- Independent DAPI, Channel 1, and Channel 2 visibility controls.
- Additive colors for objects positive in multiple selected marker layers.
- Small, medium, and large QC marker sizes.
- Versioned preview-coordinate export that avoids loading the original stitched
  TIFF into the Python interface.

## 1.1.0 — 2026-09-22

### Added

- Results Preview tab with checkbox-controlled CSV columns.
- Count, physical-measurement, percentage, and show-all presets.
- Calibrated Allen leaf-region area in mm².
- Two-dimensional object densities per mm².
- Positive-population percentage columns.
- Complete zero-count Allen leaf-region rows.

### Changed

- Redesigned four-tab workflow interface.
- Versioned regional CSV, atlas overlay, checkpoint, and threshold-report output
  names use schema version 1.1.
- Results Preview remains compatible with older versioned regional CSV files.

## 1.0.0

- Initial Brain Cell Counter release.
