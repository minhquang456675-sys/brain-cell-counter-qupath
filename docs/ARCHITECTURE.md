# Architecture and data flow

## Components

| Component | Source | Responsibility |
|---|---|---|
| Launcher | `run_brain_cell_counter_v1.0.py` | Starts the desktop application. |
| Shared application core | `brain_cell_counter/app_core.py` | Toolkit-independent project parsing, parameter validation, and QuPath command construction. |
| Qt GUI | `brain_cell_counter/qt_app.py` | Default modern interface and sequential validation, analysis, and atlas-import queues. |
| Classic GUI | `brain_cell_counter/hybrid_app.py` | Compatibility interface for calibration and threshold workflows while their Qt pages are completed. |
| Threshold optimizer | `brain_cell_counter/threshold_optimizer.py` | Dataset-level threshold calculations and CSV, JSON, and HTML reports. |
| Calibration model | `brain_cell_counter/calibration.py` | Scale-bar endpoint proposals, physical-unit conversion, project matching, manifests, and backups. |
| Calibration wizard | `brain_cell_counter/calibration_wizard.py` | Sequential multi-image preview generation, human review, and confirmed QuPath metadata writes. |
| Fiji calibration exporter | `Brain_Cell_Counter_Fiji_Calibration_v1.0.groovy` | Writes a new calibrated OME-TIFF through Bio-Formats and verifies geometry, channels, bit depth, and physical pixel size. |
| Results preview | `brain_cell_counter/results_preview.py` | Discovers regional CSV outputs, defaults to essential columns, exports user-selected compact CSVs, and opens the exact saved image entry in the QuPath desktop viewer. |
| QuPath engine | `Brain_Cell_Counter_QuPath_Engine_v1.0.groovy` | Cellpose execution/reuse, measurement, classification, Allen-region assignment, QuPath save, and export. |
| Installer source | `installer/` | Downloads and verifies pinned third-party dependencies and installs the application. |

Generated copies under `dist/` and `installer/build/` are not sources of truth.

## Processing pipeline

```text
Fluorescence images
    |
    +--> CALIBRATE (optional, human-confirmed metadata correction)
    |
    v
ABBA + DeepSlice Local registration
    |
    v
QuPath project with Allen atlas annotations
    |
    +--> IMPORT ABBA (when exported annotations are not yet loaded)
    |
    +--> PREFLIGHT (source-image URI checks and optional repair)
    |
    +--> VALIDATE (non-saving readiness checks)
    |
    v
Python GUI creates a sequential analysis queue
    |
    v
One headless QuPath process per selected entry
    |
    v
Cellpose segmentation or compatible detection reuse
    |
    v
Marker classification + Allen leaf-region assignment
    |
    +--> regional CSV
    +--> high-resolution atlas overlay PNG
    +--> saved QuPath entry
    +--> batch checkpoint
```

## Concurrency model

Entries are processed strictly in QuPath project order, one at a time. The next
entry starts only after the current QuPath process exits. This prevents
simultaneous writes to the same project and prevents multiple Cellpose jobs
from competing for GPU memory.

One failed or missing entry is logged and skipped; the remaining queue
continues. Cancel stops the current QuPath process and prevents the next entry
from starting.

## Project safety

PREFLIGHT checks the source-image URI for every project entry. If an image is
missing, the application can search a selected folder recursively by exact
filename. It proposes only unique matches, leaves ambiguous or unresolved
links unchanged, asks before writing, and creates a timestamped
`project.qpproj.preflight-backup-*.bak` backup before applying repairs.

VALIDATE checks entries sequentially without saving changes. Missing sources,
QuPath open failures, and entries that are not analysis-ready are reported
separately.

CALIBRATE renders bounded previews sequentially through QuPath so large
stitched TIFFs are not loaded concurrently. Automatic line detection proposes
scale-bar endpoints, but a person must confirm or redraw the endpoints and
enter the printed physical distance. Only confirmed records matched to a
unique project entry can be applied. Before writing, the application backs up
`project.qpproj` and each affected `data.qpdata`. Source images are never
resampled or overwritten.

After confirmation, Fiji export is independent of QuPath project matching. It
opens one source at a time as a virtual Bio-Formats stack, writes a new
OME-TIFF under `fiji_calibrated/`, reopens the result, and records a JSON
verification report. A record is marked verified only when dimensions,
channels, slices, frames, bit depth, and physical pixel size match.

## Atlas hierarchy and detections

The engine uses a separate `__BCC_ANALYSIS_ROI__` as the Cellpose parent. Each
detection is assigned to one leaf Allen region, and region IDs are mapped
through the project ontology.

If an older Cellpose run replaced the children of the atlas `Root`, validation
or analysis rebuilds the Allen hierarchy from the entry's original ABBA ROI
set. The engine rebuilds a split or unsplit atlas only when needed for the
selected hemisphere mode.

## Batch checkpoint

After each successful entry, ANALYZE updates:

```text
<QuPath project>/brain_cell_counter_results/batch_checkpoint_v2.0.json
```

The application can skip completed entries only when the project and exact
analysis parameters match the checkpoint signature. Changing any analysis
parameter creates a different signature and is treated as a new run.

## Calibration outputs

```text
<QuPath project>/brain_cell_counter_calibration/calibration_manifest_v1.2.csv
<QuPath project>/brain_cell_counter_calibration/calibration_audit_v1.2.json
<QuPath project>/brain_cell_counter_calibration/qc/*_preview.png
<QuPath project>/brain_cell_counter_calibration/fiji_calibrated/*_calibrated.ome.tif
<QuPath project>/brain_cell_counter_calibration/fiji_verification/*_verification.json
```

The compact CSV records essential image identity, source geometry, scale-bar
length and label, `pixel_width_mm`, `pixel_height_mm`, confirmation/application
status, calibrated output, and notes. The JSON audit preserves preview details,
endpoint coordinates, metadata calibration, and all internal record fields.

## Outputs

For every successful entry:

```text
<QuPath project>/brain_cell_counter_results/<image>_regional_counts_v2.0.csv
<QuPath project>/brain_cell_counter_results/<image>_atlas_overlay_v2.0.png
```

The overlay is approximately 3200 pixels and contains the source fluorescence
image, semitransparent Allen-region colors, white borders, leaf-region
acronyms, and the hemisphere mode. Crowded labels are moved to nearby free
positions and connected with leader lines.

Results Preview reads the exact image-entry name from the regional CSV. **OPEN
IN QUPATH** starts the windowed QuPath launcher with that project and entry, so
the user inspects the saved full-resolution detections through QuPath itself.
The button is disabled while a headless project operation is active to prevent
simultaneous access.

Each CSV includes `Major_Group`, derived from Allen ontology ancestors.
Level-5 Allen divisions are used for gray-matter regions, and Hippocampal
formation (`HPF`) is reported as `Hippocampus` so its descendants can be
summarized together.

The engine also reports every Allen leaf region, including zero-count regions.
When image calibration is valid it appends region area in mm², densities per
mm², and marker-positive percentages. Physical fields are blank when
calibration is unavailable; the application does not assume 1 µm/pixel.

The master regional CSV remains complete and versioned. Results Preview can
write an additional `_selected.csv` containing only the columns chosen by the
user. Compact export reads every source row and never rewrites the master file.
