"""Pure calibration helpers for scale-bar proposals and audited metadata writes.

Automatic detection proposes endpoints only. Physical distance and final
calibration always require explicit human confirmation in the GUI.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image


SUPPORTED_IMAGE_SUFFIXES = (
    ".tif",
    ".tiff",
    ".ome.tif",
    ".ome.tiff",
    ".png",
    ".jpg",
    ".jpeg",
)


@dataclass
class ScaleBarCandidate:
    """Describe one automatically proposed line on a bounded preview image."""
    orientation: str
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    preview_length_pixels: float
    confidence: float


@dataclass
class CalibrationRecord:
    """Store the complete review and application state for one source image."""
    source_path: str
    image_name: str = ""
    entry_id: int | None = None
    source_width: int | None = None
    source_height: int | None = None
    preview_width: int | None = None
    preview_height: int | None = None
    downsample: float | None = None
    preview_path: str = ""
    metadata_pixel_width_microns: float | None = None
    metadata_pixel_height_microns: float | None = None
    bar_start_x: int | None = None
    bar_start_y: int | None = None
    bar_end_x: int | None = None
    bar_end_y: int | None = None
    bar_preview_length_pixels: float | None = None
    known_distance: float | None = None
    known_unit: str = "µm"
    proposed_pixel_size_microns: float | None = None
    detection_confidence: float | None = None
    confirmed: bool = False
    applied: bool = False
    fiji_output_path: str = ""
    fiji_verified_pixel_width_microns: float | None = None
    fiji_verified_pixel_height_microns: float | None = None
    fiji_verified: bool = False
    note: str = ""

    def calculate_proposal(self) -> float | None:
        if (
            self.known_distance is None
            or self.known_distance <= 0
            or self.bar_preview_length_pixels is None
            or self.bar_preview_length_pixels <= 0
            or self.downsample is None
            or self.downsample <= 0
        ):
            self.proposed_pixel_size_microns = None
            return None
        distance_microns = self.known_distance * (1000.0 if self.known_unit == "mm" else 1.0)
        original_bar_pixels = self.bar_preview_length_pixels * self.downsample
        self.proposed_pixel_size_microns = distance_microns / original_bar_pixels
        return self.proposed_pixel_size_microns


def is_supported_image(path: Path) -> bool:
    """Return whether a path has a supported microscopy or preview suffix."""
    name = path.name.lower()
    return path.is_file() and any(name.endswith(suffix) for suffix in SUPPORTED_IMAGE_SUFFIXES)


def _longest_true_run(values: list[bool]) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    run_start: int | None = None
    for index, value in enumerate(values + [False]):
        if value and run_start is None:
            run_start = index
        elif not value and run_start is not None:
            run = (run_start, index - 1)
            if best is None or run[1] - run[0] > best[1] - best[0]:
                best = run
            run_start = None
    return best


def detect_scale_bar(preview_path: Path) -> ScaleBarCandidate | None:
    """Propose the longest bright achromatic line near a preview margin."""
    """Detect a bright achromatic line near an image margin.

    This proposes endpoints only. The GUI always requires human confirmation
    of both endpoints and the physical label before calibration can be applied.
    """
    with Image.open(preview_path) as source:
        image = source.convert("RGB")
    width, height = image.size
    pixels = image.load()
    margin_x = max(1, int(width * 0.22))
    margin_y = max(1, int(height * 0.22))

    def bright(x: int, y: int) -> bool:
        red, green, blue = pixels[x, y]
        return min(red, green, blue) >= 170 and max(red, green, blue) - min(red, green, blue) <= 45

    candidates: list[ScaleBarCandidate] = []
    x_ranges = [range(0, margin_x), range(max(0, width - margin_x), width)]
    for x_range in x_ranges:
        for x in x_range:
            run = _longest_true_run([bright(x, y) for y in range(height)])
            if run is None:
                continue
            start, end = run
            length = end - start
            if length < max(18, int(height * 0.025)):
                continue
            edge_distance = min(x, width - 1 - x) / max(1.0, width * 0.22)
            confidence = min(0.98, 0.50 + length / max(width, height) * 2.5 + (1.0 - edge_distance) * 0.20)
            candidates.append(ScaleBarCandidate("vertical", x, start, x, end, float(length), confidence))

    y_ranges = [range(0, margin_y), range(max(0, height - margin_y), height)]
    for y_range in y_ranges:
        for y in y_range:
            run = _longest_true_run([bright(x, y) for x in range(width)])
            if run is None:
                continue
            start, end = run
            length = end - start
            if length < max(18, int(width * 0.025)):
                continue
            edge_distance = min(y, height - 1 - y) / max(1.0, height * 0.22)
            confidence = min(0.98, 0.50 + length / max(width, height) * 2.5 + (1.0 - edge_distance) * 0.20)
            candidates.append(ScaleBarCandidate("horizontal", start, y, end, y, float(length), confidence))

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.confidence, item.preview_length_pixels))


def file_uri_to_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme.lower() != "file":
        return None
    decoded = unquote(parsed.path)
    if parsed.netloc:
        decoded = f"//{parsed.netloc}{decoded}"
    if len(decoded) >= 3 and decoded[0] == "/" and decoded[2] == ":":
        decoded = decoded[1:]
    return Path(decoded)


def _find_uri(value: object) -> str | None:
    if isinstance(value, dict):
        uri = value.get("uri")
        if isinstance(uri, str):
            return uri
        for nested in value.values():
            result = _find_uri(nested)
            if result:
                return result
    elif isinstance(value, list):
        for nested in value:
            result = _find_uri(nested)
            if result:
                return result
    return None


def read_project_source_entries(project_path: Path) -> list[dict[str, object]]:
    """Read QuPath entry identifiers and source URIs without modifying the project."""
    data = json.loads(project_path.read_text(encoding="utf-8-sig"))
    entries: list[dict[str, object]] = []
    for item in data.get("images", []):
        image_name = str(item.get("imageName", ""))
        entry_id = item.get("entryID")
        uri = _find_uri(item.get("serverBuilder"))
        source_path = file_uri_to_path(uri) if uri else None
        if image_name and entry_id is not None:
            entries.append({
                "image_name": image_name,
                "entry_id": int(entry_id),
                "source_path": source_path,
            })
    return entries


def match_records_to_project(records: list[CalibrationRecord], project_path: Path) -> None:
    """Match records only when a source file maps uniquely to one project entry."""
    entries = read_project_source_entries(project_path)
    by_resolved_path: dict[str, list[dict[str, object]]] = {}
    by_filename: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        source_path = entry.get("source_path")
        if isinstance(source_path, Path):
            try:
                key = str(source_path.resolve()).casefold()
            except OSError:
                key = str(source_path.absolute()).casefold()
            by_resolved_path.setdefault(key, []).append(entry)
            by_filename.setdefault(source_path.name.casefold(), []).append(entry)

    for record in records:
        source = Path(record.source_path)
        try:
            path_key = str(source.resolve()).casefold()
        except OSError:
            path_key = str(source.absolute()).casefold()
        matches = by_resolved_path.get(path_key, [])
        if not matches:
            matches = by_filename.get(source.name.casefold(), [])
        if len(matches) == 1:
            record.image_name = str(matches[0]["image_name"])
            record.entry_id = int(matches[0]["entry_id"])
        elif len(matches) > 1:
            record.note = "Ambiguous project filename; exact source path did not match."
        else:
            record.note = "File is not an entry in the selected QuPath project."


CALIBRATION_SUMMARY_FIELDS = (
    "source_file",
    "source_path",
    "qupath_image_name",
    "qupath_entry_id",
    "image_width_px",
    "image_height_px",
    "scale_bar_length_px",
    "known_distance",
    "known_unit",
    "pixel_width_mm",
    "pixel_height_mm",
    "confirmed",
    "qupath_applied",
    "fiji_verified",
    "calibrated_output_path",
    "note",
)


def _positive_finite(value: float | None) -> float | None:
    """Return a positive finite number or None for missing/invalid values."""
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return value


def _effective_pixel_dimensions_mm(record: CalibrationRecord) -> tuple[float | None, float | None]:
    """Return verified or proposed per-pixel dimensions in millimetres."""
    width_microns = (
        record.fiji_verified_pixel_width_microns
        if record.fiji_verified and record.fiji_verified_pixel_width_microns is not None
        else record.proposed_pixel_size_microns
    )
    height_microns = (
        record.fiji_verified_pixel_height_microns
        if record.fiji_verified and record.fiji_verified_pixel_height_microns is not None
        else record.proposed_pixel_size_microns
    )
    width_microns = _positive_finite(width_microns)
    height_microns = _positive_finite(height_microns)
    return (
        width_microns / 1000.0 if width_microns is not None else None,
        height_microns / 1000.0 if height_microns is not None else None,
    )


def _calibration_summary_row(record: CalibrationRecord) -> dict[str, object]:
    """Build one compact CSV row while retaining essential traceability."""
    pixel_width_mm, pixel_height_mm = _effective_pixel_dimensions_mm(record)
    preview_length = _positive_finite(record.bar_preview_length_pixels)
    downsample = _positive_finite(record.downsample)
    scale_bar_length = (
        preview_length * downsample
        if preview_length is not None and downsample is not None
        else None
    )
    return {
        "source_file": Path(record.source_path).name,
        "source_path": record.source_path,
        "qupath_image_name": record.image_name,
        "qupath_entry_id": record.entry_id if record.entry_id is not None else "",
        "image_width_px": record.source_width if record.source_width is not None else "",
        "image_height_px": record.source_height if record.source_height is not None else "",
        "scale_bar_length_px": scale_bar_length if scale_bar_length is not None else "",
        "known_distance": record.known_distance if record.known_distance is not None else "",
        "known_unit": record.known_unit,
        "pixel_width_mm": pixel_width_mm if pixel_width_mm is not None else "",
        "pixel_height_mm": pixel_height_mm if pixel_height_mm is not None else "",
        "confirmed": record.confirmed,
        "qupath_applied": record.applied,
        "fiji_verified": record.fiji_verified,
        "calibrated_output_path": record.fiji_output_path,
        "note": record.note,
    }


def write_calibration_manifest(records: list[CalibrationRecord], output_path: Path) -> Path:
    """Write a compact, human-readable calibration results table."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CALIBRATION_SUMMARY_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(_calibration_summary_row(record))
    return output_path


def write_calibration_audit(records: list[CalibrationRecord], output_path: Path) -> Path:
    """Preserve the complete calibration state in a machine-readable JSON audit."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "1.2",
        "records": [asdict(record) for record in records],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def backup_project_calibration_targets(
    project_path: Path,
    records: list[CalibrationRecord],
) -> list[Path]:
    """Create timestamped backups before any confirmed QuPath metadata write."""
    import shutil

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: list[Path] = []
    project_backup = project_path.with_name(project_path.name + f".calibration-backup-{timestamp}.bak")
    shutil.copy2(project_path, project_backup)
    backups.append(project_backup)
    for entry_id in sorted({record.entry_id for record in records if record.entry_id is not None}):
        source = project_path.parent / "data" / str(entry_id) / "data.qpdata"
        if not source.is_file():
            continue
        backup = source.with_name(source.name + f".calibration-backup-{timestamp}.bak")
        shutil.copy2(source, backup)
        backups.append(backup)
    return backups


def valid_confirmed_records(records: list[CalibrationRecord]) -> list[CalibrationRecord]:
    """Return only human-confirmed records with a valid unique project mapping."""
    return [
        record
        for record in records
        if record.confirmed
        and record.entry_id is not None
        and record.image_name
        and record.proposed_pixel_size_microns is not None
        and math.isfinite(record.proposed_pixel_size_microns)
        and record.proposed_pixel_size_microns > 0
    ]


def valid_confirmed_fiji_records(records: list[CalibrationRecord]) -> list[CalibrationRecord]:
    """Return confirmed source files that can be exported independently of QuPath."""
    return [
        record
        for record in records
        if record.confirmed
        and Path(record.source_path).is_file()
        and record.proposed_pixel_size_microns is not None
        and math.isfinite(record.proposed_pixel_size_microns)
        and record.proposed_pixel_size_microns > 0
    ]


def next_fiji_output_path(output_directory: Path, source_path: Path) -> Path:
    """Choose a new calibrated OME-TIFF filename without overwriting prior exports."""
    lower_name = source_path.name.lower()
    if lower_name.endswith(".ome.tiff"):
        base_name = source_path.name[:-9]
    elif lower_name.endswith(".ome.tif"):
        base_name = source_path.name[:-8]
    else:
        base_name = source_path.stem
    candidate = output_directory / f"{base_name}_calibrated.ome.tif"
    suffix = 2
    while candidate.exists():
        candidate = output_directory / f"{base_name}_calibrated_{suffix}.ome.tif"
        suffix += 1
    return candidate
