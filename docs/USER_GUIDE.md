# User guide

## Interface layout

Version 2 opens the integrated PySide6/Qt interface by default. It adds rounded
cards and controls, hover and pressed feedback, stable immediate page switching,
subtle elevation, automatic monitor sizing, and a fixed workflow sidebar. Global
threshold optimization, Results Preview, project-link repair, and Cellpose
parameter suggestion are available directly in this interface. **Open
calibration** starts a native PySide6 three-step human-review workspace with
the same ChatGPT-inspired styling and live Images, Scanned, Confirmed, and
Project matched metrics; it does not open the classic application shell.
Existing records are restored from the calibration JSON audit, and accessible
images from the selected QuPath project are listed automatically on opening.

The current project bar stays visible across the application and reports the
project name, entry count, selected image, and ABBA readiness.

The interface uses a ChatGPT-inspired light palette: a white workspace, subtle
gray cards, graphite text, dark primary buttons, and restrained green accents
for progress, help cues, and successful states.

The progress area uses a status badge: READY before work, RUNNING during a
QuPath/Cellpose operation, COMPLETE after success, and ERROR or CANCELLED when
attention is required. Keyboard shortcuts are Ctrl+O for project selection,
Ctrl+L for the run log, and F1 for Help.

The main window follows the scientific workflow:

- **1 START** selects the QuPath project and groups Calibration, Atlas import,
  and File-link preparation.
- **2 ANALYSIS** separates channel names, positive-intensity thresholds, and
  analysis behavior into numbered sections. Cellprob, Flow, and automatic
  Cellpose suggestions are available through **Advanced Cellpose settings…**
  and **AUTO SUGGEST CELLPOSE**.
- **3 THRESHOLDS** includes dataset scope, threshold type, background guard,
  threshold lock, candidate ranges, plateau criteria, and the run button.
- **4 RESULTS** loads regional CSV files and provides measurement presets.
  **Choose columns…** opens the complete checkbox list in a separate scrollable
  Qt window so long column names do not widen the main application. **Regional
  table** and **Atlas overlay** tabs show the measurements and the exact
  version-matched QC image without loading the full-resolution source TIFF.

Closing the calibration or column-selection window does not modify the master
result files. Threshold and analysis values remain visible in their pages and
are stored with the application settings.

The **VALIDATE**, **ANALYZE**, cancellation, progress, run-log, and results
controls remain visible at the bottom while switching tabs. Use **VALIDATE**
before **ANALYZE** whenever the project or analysis settings change.

## Results preview and physical measurements

After an analysis finishes, open **4 RESULTS** and select a regional
CSV. The **Essential** preset is selected by default and keeps only image and
atlas identifiers, hemisphere, area, and cell counts visible. Use **Counts**,
**Physical metrics**, **Percentages**, or **Show all** only when those columns
are required. Use **Choose columns…** to change individual checkboxes.

Click **EXPORT VISIBLE CSV** to create a compact CSV containing exactly the
currently selected columns and every result row. The versioned master regional
CSV is never modified. This preserves a complete scientific record while
providing a smaller table for Excel, statistics, or figures.

Click **OPEN IN QUPATH** to open the selected result's exact project image entry
in the normal QuPath desktop viewer. The entry name is read from the CSV rather
than inferred from its filename. The button is disabled during VALIDATE,
ANALYZE, IMPORT ABBA, and calibration so the desktop viewer cannot compete with
a headless process for the same project.

QuPath displays the saved full-resolution detections and provides its standard
overlay and classification visibility controls. Channel 1-positive objects are
the Channel 1-only and triple-positive classes; Channel 2-positive objects are
the Channel 2-only and triple-positive classes; DAPI-positive objects are all
four valid DAPI classes. Close the QuPath desktop window before returning to an
operation that writes the project.

When valid pixel calibration is available, new analyses append:

- pixel width and height in micrometres,
- Allen leaf-region area in square millimetres,
- valid-object, Channel 1, Channel 2, and double-positive densities per square
  millimetre,
- positive fractions relative to the valid Channel 3 object population and
  double-positive fractions relative to each positive marker population.

Physical fields remain blank when calibration is missing or invalid. These are
two-dimensional profile densities, not stereological cell densities per cubic
millimetre. Existing CSV columns remain unchanged and the new measurements are
appended at the end.

## Before analysis

1. Complete ABBA/DeepSlice registration and export regions to the QuPath
   project.
2. Click **IMPORT ABBA** when registration files exist but atlas annotations
   have not yet been loaded into the QuPath entries. The importer processes
   registered entries sequentially and reports entries that still need
   registration.
3. Close the QuPath desktop window to prevent simultaneous project access.
4. Start Brain Cell Counter and select `project.qpproj`.
5. Confirm that **Cellpose Python** points to the configured environment,
   normally `%USERPROFILE%\miniforge3\envs\cellpose\python.exe`.

## Batch calibration for stitched images

Use **CALIBRATE** before ABBA when TIFF metadata is missing or conflicts with a
burned-in scale bar.

1. Click **CALIBRATE**.
2. Use **ADD IMAGES**, **ADD FOLDER**, or **ADD ALL PROJECT IMAGES**. Multiple
   files are accepted at once.
3. Click **SCAN SCALE BARS**. Images are opened sequentially through QuPath;
   only bounded 4000-pixel QC previews are created.
4. The review window opens automatically. Inspect the red proposed line. Click
   the two true scale-bar endpoints if the proposal is wrong.
5. Enter the distance printed on that image and choose `µm` or `mm`.
6. Click **CONFIRM & NEXT**. Confirmation automatically advances to the next
   scanned image.
7. Review the table, then choose one output:
   - **EXPORT CALIBRATED WITH FIJI** writes new `_calibrated.ome.tif` copies,
     reopens them, and verifies dimensions, channels, bit depth, and µm/pixel.
     Project matching is not required.
   - **APPLY CONFIRMED TO QUPATH** updates only backed-up QuPath metadata and
     leaves the source files unchanged.

The Calibration table uses green rows for confirmed/applied/verified records,
amber rows for records awaiting review, and red rows for records needing
attention. Actions remain disabled until the required scan, confirmation, or
project match exists.

Unconfirmed rows cannot be applied. Files not matched uniquely to the selected
QuPath project can be included in the manifest but cannot change the project.
Before a confirmed write, the application backs up `project.qpproj` and every
affected `data.qpdata`. TIFF pixels are never resized or overwritten.

Outputs are stored in `brain_cell_counter_calibration/`, including the CSV
manifest, QC previews, `fiji_calibrated/` OME-TIFF copies, and
`fiji_verification/` JSON reports. Existing source and calibrated files are
never overwritten; repeated exports receive a numbered filename. Do not treat
automatic endpoints as proof of correct calibration; inspect every image and
resolve metadata conflicts.

The compact `calibration_manifest_v1.2.csv` file includes only essential
identity, scale, status, and output fields, including `pixel_width_mm` and
`pixel_height_mm`. The complete technical record is preserved separately in
`calibration_audit_v1.2.json`.

## Preflight and validation

Run **PREFLIGHT** after moving or copying a QuPath project. It checks whether
every entry can access its source image and reports `OK` or `MISSING`. Missing
links can be repaired from a chosen search folder only when an exact filename
has one unique match. The application asks before writing and creates a backup.

Run **VALIDATE** before analysis. It checks all registered entries without
saving changes. A failed entry does not stop validation of the remaining
entries.

## Channels and thresholds

Default channel meanings are:

- Channel 1 (red): NeuN
- Channel 2 (green): c-Fos
- Channel 3 (blue): DAPI

The names are editable. **Save channel names** stores them for later use;
validation and analysis also save the current names. These names are used in
QuPath classes and regional CSV headers.

Review validation results and temporary thresholds before starting a batch.
See [Scientific method and validation](SCIENTIFIC_METHOD.md) before locking a
dataset-wide threshold.

## Hemisphere modes

- **Left and Right** uses separate ABBA annotations and labels for both
  hemispheres. If the entry has a "Do not split" atlas, the engine rebuilds it
  from the ABBA ROI set.
- **Do not split** combines both hemispheres into one `Combined` row per Allen
  region. A previously split atlas is rebuilt in unsplit mode.

## Cellpose parameter suggestion

**AUTO SUGGEST CELLPOSE** performs a non-saving calibration on the selected
image. It tests five Cellprob values around the current value and three Flow
values around the current value, for 15 combinations on three representative
640 x 640 crops.

The recommendation favors locally stable mask counts, excludes settings that
lose more than half of the maximum masks, and breaks ties toward a higher
Cellprob and lower Flow value. This is a heuristic starting point, not
biological ground truth. Inspect masks visually before analyzing the full
batch.

## Analyze a batch

1. Click **ANALYZE**.
2. Select the required project entries.
3. Confirm parameters and start the queue.

Selected entries are sorted by their position in the QuPath project and run
sequentially. Every entry uses the same engine, parameters, validation,
segmentation/reuse, classification, regional export, atlas-overlay export, and
save procedure.

The main progress bar shows the current entry and overall batch percentage.
The separate run-log window reports engine stages, CUDA activation, Cellpose
progress, model loading, mask import, entry completion, and errors. Closing the
log window only hides it; use **Open run log** to show it again.

After each successful entry, a checkpoint is saved. On a later run with the
same project and exact parameters, the application can skip completed entries.
Choosing rerun removes the selected entries from the active completion set and
processes them again.

## Settings

The Settings dialog contains:

- QuPath Console path
- Fiji launcher path
- Cellpose Python path
- interface font size
- run-log font size
- main-window size preset

**Recommended** fits the current monitor automatically. Compact, Large, and
Maximized presets are also available. Settings are stored locally in
`brain_cell_counter_settings.json`; this file is machine-specific and is not
committed to Git.

Hover over a blue **?** beside a parameter to see its purpose, the effect of
increasing or decreasing it, and whether it changes segmentation or only
classification.
