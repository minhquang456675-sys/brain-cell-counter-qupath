"""Shared project and QuPath-process primitives for desktop front ends.

This module deliberately contains no Tk or Qt imports.  Keeping project parsing,
argument validation, and command construction here prevents a visual redesign
from silently changing the scientific analysis request sent to QuPath.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .version import APP_VERSION, OUTPUT_SCHEMA_VERSION


ENGINE_FILENAME = "Brain_Cell_Counter_QuPath_Engine_v1.0.groovy"
QUPATH_SCRIPTS_DIRECTORY = "qupath_scripts"


def qupath_script_path(filename: str) -> Path:
    """Resolve a packaged QuPath/Fiji script from its categorized folder."""
    return Path(__file__).resolve().parent.parent / QUPATH_SCRIPTS_DIRECTORY / filename


@dataclass(frozen=True)
class ProjectEntry:
    """One image entry in its original QuPath project order."""

    name: str
    entry_id: int
    source_path: Path | None
    source_exists: bool
    abba_roi: bool
    abba_transform: bool

    @property
    def abba_ready(self) -> bool:
        """Return whether entry-specific ABBA files are present."""
        return self.abba_roi and self.abba_transform


@dataclass(frozen=True)
class ProjectSummary:
    """Read-only summary used by both the classic and Qt interfaces."""

    path: Path
    entries: tuple[ProjectEntry, ...]
    ontology_found: bool

    @property
    def analysis_ready_count(self) -> int:
        """Count entries with source files and complete atlas inputs."""
        return sum(
            entry.source_exists and entry.abba_ready and self.ontology_found
            for entry in self.entries
        )


def file_uri_to_path(uri: str) -> Path | None:
    """Convert a Windows file URI to a local path without touching the file."""
    parsed = urlparse(uri)
    if parsed.scheme.lower() != "file":
        return None
    raw_path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", raw_path):
        raw_path = raw_path[1:]
    return Path(raw_path.replace("/", os.sep))


def find_file_uri(value: object) -> str | None:
    """Find the first local source URI inside a QuPath image entry."""
    if isinstance(value, dict):
        uri = value.get("uri")
        if isinstance(uri, str) and uri.lower().startswith("file:"):
            return uri
        for nested in value.values():
            found = find_file_uri(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = find_file_uri(nested)
            if found is not None:
                return found
    return None


def read_project(project_path: Path) -> ProjectSummary:
    """Read a QuPath project and preserve its image-entry order."""
    path = Path(project_path).resolve()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    ontology_found = any(path.parent.glob("*-Ontology.json"))
    entries: list[ProjectEntry] = []
    for raw_entry in data.get("images", []):
        if "imageName" not in raw_entry or "entryID" not in raw_entry:
            continue
        name = str(raw_entry["imageName"])
        entry_id = int(raw_entry["entryID"])
        uri = find_file_uri(raw_entry)
        source_path = file_uri_to_path(uri) if uri else None
        entry_folder = path.parent / "data" / str(entry_id)
        entries.append(
            ProjectEntry(
                name=name,
                entry_id=entry_id,
                source_path=source_path,
                source_exists=bool(source_path and source_path.is_file()),
                abba_roi=any(entry_folder.glob("ABBA-RoiSet-*")),
                abba_transform=any(entry_folder.glob("ABBA-Transform-*")),
            )
        )
    return ProjectSummary(path=path, entries=tuple(entries), ontology_found=ontology_found)


def analysis_arguments(settings: dict[str, object], mode: str) -> list[str]:
    """Validate UI values and create the exact key/value arguments for Groovy."""
    names = [str(settings[f"channel_{index}_name"]).strip() for index in range(1, 4)]
    if any(not name for name in names):
        raise ValueError("Each channel must have a non-empty name.")
    channels = [int(settings.get(f"channel_{index}", index)) for index in range(1, 4)]
    if min(channels) < 1:
        raise ValueError("Channel numbers are one-based and must be at least 1.")
    cellpose_python = Path(str(settings["cellpose_python"]).strip())
    if not cellpose_python.is_file():
        raise ValueError("Choose the existing python.exe from the Cellpose environment.")
    hemisphere = str(settings.get("hemisphere_mode", "Left and Right"))
    values: dict[str, object] = {
        "mode": mode,
        "neunChannel": channels[0],
        "cfosChannel": channels[1],
        "dapiChannel": channels[2],
        "neunThreshold": float(settings["channel_1_threshold"]),
        "cfosThreshold": float(settings["channel_2_threshold"]),
        "dapiThreshold": float(settings["channel_3_threshold"]),
        "requireDapiThreshold": str(bool(settings.get("require_channel_3", False))).lower(),
        "cellprobThreshold": float(settings.get("cellprob", -0.5)),
        "flowThreshold": float(settings.get("flow", 0.4)),
        "reuseDetections": str(bool(settings.get("reuse_detections", True))).lower(),
        "cellposePythonPath": str(cellpose_python),
        "channel1Name": names[0],
        "channel2Name": names[1],
        "channel3Name": names[2],
        "hemisphereMode": "left-right" if hemisphere == "Left and Right" else "do-not-split",
        "channel1ThresholdStart": float(settings.get("channel_1_start", 0)),
        "channel1ThresholdEnd": float(settings.get("channel_1_end", 200)),
        "channel1ThresholdStep": float(settings.get("channel_1_step", 5)),
        "channel2ThresholdStart": float(settings.get("channel_2_start", 0)),
        "channel2ThresholdEnd": float(settings.get("channel_2_end", 200)),
        "channel2ThresholdStep": float(settings.get("channel_2_step", 5)),
        "channel3ThresholdStart": float(settings.get("channel_3_start", 0)),
        "channel3ThresholdEnd": float(settings.get("channel_3_end", 200)),
        "channel3ThresholdStep": float(settings.get("channel_3_step", 5)),
        "thresholdPair": str(settings.get("threshold_pair", "channel1-channel2")),
    }
    return [f"{key}={value}" for key, value in values.items()]


def qupath_command(
    qupath: Path,
    project: Path,
    image_name: str,
    engine: Path,
    arguments: list[str],
    save: bool,
) -> list[str]:
    """Build one headless QuPath command for a single project entry."""
    command = [str(qupath), "-q", "script", "-p", str(project), "-i", image_name]
    if save:
        command.append("--save")
    command.append(str(engine))
    for argument in arguments:
        command.extend(["--args", argument])
    return command


def checkpoint_path(project: Path) -> Path:
    """Return the versioned analysis checkpoint beside other project outputs."""
    return Path(project).parent / "brain_cell_counter_results" / f"batch_checkpoint_v{OUTPUT_SCHEMA_VERSION}.json"


def analysis_signature(project: Path, arguments: list[str]) -> str:
    """Hash the project and exact engine arguments to prevent unsafe reuse."""
    payload = json.dumps(
        {
            "project": str(Path(project).resolve()),
            "output_schema": OUTPUT_SCHEMA_VERSION,
            "arguments": arguments,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_checkpoint(path: Path, signature: str) -> set[str]:
    """Read completed entries only when the saved parameter signature matches."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("signature") != signature:
            return set()
        return {str(name) for name in data.get("completed", [])}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return set()


def write_checkpoint(
    path: Path,
    project: Path,
    signature: str,
    completed: set[str],
    failed: list[str],
) -> None:
    """Atomically persist completed entries after each successful analysis."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "version": APP_VERSION,
                "output_schema": OUTPUT_SCHEMA_VERSION,
                "project": str(Path(project).resolve()),
                "signature": signature,
                "completed": sorted(completed),
                "failed": list(failed),
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
