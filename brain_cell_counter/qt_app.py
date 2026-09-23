"""Modern Qt front end for Brain Cell Counter.

The interface keeps the scientific engine unchanged.  It sends one QuPath
entry at a time through the shared command builder so project writes and GPU
work remain strictly sequential.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import ctypes
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .app_core import (
    SUPPORTED_CELLPOSE_MODELS,
    ENGINE_FILENAME,
    analysis_arguments,
    analysis_signature,
    checkpoint_path,
    file_uri_to_path,
    qupath_command,
    qupath_script_path,
    read_checkpoint,
    read_project,
    write_checkpoint,
)
from .version import APP_VERSION_LABEL
from .threshold_optimizer import calculate_global_thresholds, write_threshold_reports
from .qt_results import QtResultsWidget


ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT / "brain_cell_counter_settings.json"
DEFAULT_QUPATH = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "QuPath-0.7.0" / "QuPath-0.7.0 (console).exe"
DEFAULT_CELLPOSE = Path.home() / "miniforge3" / "envs" / "cellpose" / "python.exe"
RUNTIME_ERROR_FILE = ROOT / "brain_cell_counter_runtime_error.log"
FIJI_CANDIDATES = (
    Path.home() / "Downloads" / "fiji-latest-win64-jdk" / "Fiji" / "fiji-windows-x64.exe",
    Path.home() / "Fiji.app" / "ImageJ-win64.exe",
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Fiji" / "fiji-windows-x64.exe",
)
DEFAULT_FIJI = next((path for path in FIJI_CANDIDATES if path.is_file()), FIJI_CANDIDATES[0])

INDIVIDUAL_THRESHOLD_TYPE = "Individual channels"
THRESHOLD_PAIR_TYPES = {
    "Channel 1 positive + Channel 2 positive": ("channel1", "channel2"),
    "Channel 1 positive + Channel 3 positive": ("channel1", "channel3"),
    "Channel 2 positive + Channel 3 positive": ("channel2", "channel3"),
}


STYLE = """
QWidget { color: #202123; font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow, #workspace { background: #FFFFFF; }
#sidebar { background: #F7F7F8; border-right: 1px solid #E5E5E5; }
#brand { font-size: 17pt; font-weight: 700; color: #202123; }
#version { color: #6B6B6B; }
QPushButton { border: 1px solid #D9D9DE; border-radius: 9px; padding: 9px 14px; background: #FFFFFF; }
QPushButton:hover { background: #F1F1F2; border-color: #C8C8CE; }
QPushButton:pressed { background: #E7E7E9; padding-top: 10px; padding-bottom: 8px; }
QPushButton:disabled { color: #A5A5AA; background: #F5F5F5; }
QPushButton[primary='true'] { background: #202123; color: white; border: 1px solid #202123; font-weight: 600; }
QPushButton[primary='true']:hover { background: #343541; border-color: #343541; }
QPushButton[accent='true'] { background: #10A37F; color: white; border: 1px solid #10A37F; font-weight: 600; }
QPushButton[accent='true']:hover { background: #0D8F70; }
QPushButton[nav='true'] { text-align: left; border: 0; padding: 10px 12px; background: transparent; }
QPushButton[nav='true']:hover { background: #ECECEF; }
QPushButton[nav='true'][selected='true'] { background: #E7E7EA; font-weight: 600; }
QLineEdit, QComboBox, QListWidget, QPlainTextEdit { background: white; border: 1px solid #D9D9DE; border-radius: 8px; padding: 8px; selection-background-color: #10A37F; }
QLineEdit:focus, QComboBox:focus, QListWidget:focus { border: 1px solid #10A37F; }
QComboBox::drop-down { border: 0; width: 28px; }
QFrame[card='true'] { background: #F7F7F8; border: 1px solid #E5E5E5; border-radius: 14px; }
QLabel[heading='true'] { font-size: 19pt; font-weight: 700; }
QLabel[subheading='true'] { font-size: 12pt; font-weight: 600; }
QLabel[muted='true'] { color: #6B6B6B; }
QLabel[status='true'] { background: #E8F7F2; color: #08775C; border-radius: 8px; padding: 5px 9px; font-weight: 700; }
QProgressBar { border: 0; border-radius: 4px; background: #ECECEF; height: 8px; text-align: center; color: transparent; }
QProgressBar::chunk { border-radius: 4px; background: #10A37F; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: #C7C7CC; min-height: 42px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #9B9BA1; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: 0; background: transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px 4px; }
QScrollBar::handle:horizontal { background: #C7C7CC; min-width: 42px; border-radius: 4px; }
QScrollBar::handle:horizontal:hover { background: #9B9BA1; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: 0; background: transparent; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QToolTip { background: #202123; color: white; border: 0; padding: 7px; }
"""


def _button(text: str, *, primary: bool = False, accent: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("primary", primary)
    button.setProperty("accent", accent)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def activate_main_window(window: QMainWindow) -> None:
    """Restore and foreground the main window after a shortcut launch."""
    window.showNormal()
    window.raise_()
    window.activateWindow()
    QApplication.setActiveWindow(window)
    if os.name != "nt":
        return
    try:
        handle = int(window.winId())
        user32 = ctypes.windll.user32
        no_move_or_size = 0x0001 | 0x0002 | 0x0040
        user32.ShowWindow(handle, 9)  # SW_RESTORE
        user32.SetWindowPos(handle, -1, 0, 0, 0, 0, no_move_or_size)
        user32.SetWindowPos(handle, -2, 0, 0, 0, 0, no_move_or_size)
        user32.SetForegroundWindow(handle)
    except (AttributeError, OSError, ValueError):
        # Qt activation above remains the portable fallback.
        return


def qupath_gui_executable(configured_path: Path) -> Path:
    """Prefer the windowed QuPath executable for interactive result review."""
    suffix = " (console).exe"
    if configured_path.name.lower().endswith(suffix):
        candidate = configured_path.with_name(configured_path.name[: -len(suffix)] + ".exe")
        if candidate.is_file():
            return candidate
    return configured_path


def _card(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("card", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    heading = QLabel(title)
    heading.setProperty("subheading", True)
    layout.addWidget(heading)
    if description:
        label = QLabel(description)
        label.setProperty("muted", True)
        label.setWordWrap(True)
        layout.addWidget(label)
    shadow = QGraphicsDropShadowEffect(frame)
    shadow.setBlurRadius(22)
    shadow.setOffset(0, 4)
    shadow.setColor(QColor(0, 0, 0, 24))
    frame.setGraphicsEffect(shadow)
    return frame, layout


class EntryDialog(QDialog):
    """Let a user choose project entries while preserving project order."""

    def __init__(self, names: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose image entries")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select entries to analyze. They will run from top to bottom."))
        self.list_widget = QListWidget()
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        return [
            self.list_widget.item(index).text()
            for index in range(self.list_widget.count())
            if self.list_widget.item(index).checkState() == Qt.CheckState.Checked
        ]


class SettingsDialog(QDialog):
    """Edit machine-specific executable paths without exposing window pixels."""

    def __init__(self, settings: dict[str, object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(720, 330)
        self.settings = settings
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.qupath = QLineEdit(str(settings["qupath"]))
        self.cellpose = QLineEdit(str(settings["cellpose_python"]))
        self.fiji = QLineEdit(str(settings["fiji"]))
        form.addRow("QuPath Console", self._path_row(self.qupath, "Choose QuPath", "Executable (*.exe)"))
        form.addRow("Cellpose Python", self._path_row(self.cellpose, "Choose Cellpose Python", "python.exe"))
        form.addRow("Fiji launcher", self._path_row(self.fiji, "Choose Fiji", "Executable (*.exe)"))
        layout.addLayout(form)
        note = QLabel("Window size is selected automatically from the current monitor.")
        note.setProperty("muted", True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _path_row(self, field: QLineEdit, title: str, file_filter: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        browse = _button("Browse…")
        browse.clicked.connect(lambda: self._browse(field, title, file_filter))
        layout.addWidget(browse)
        return row

    def _browse(self, field: QLineEdit, title: str, file_filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, title, field.text(), file_filter)
        if path:
            field.setText(path)


class BrainCellCounterQt(QMainWindow):
    """Modern workflow window with sequential QuPath process orchestration."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Brain Cell Counter {APP_VERSION_LABEL}")
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1320, screen.width() - 80), min(880, screen.height() - 90))
        self.setMinimumSize(1040, 720)
        self.settings = self._load_settings()
        self.summary = None
        self.queue: list[str] = []
        self.completed = 0
        self.failed: list[str] = []
        self.checkpoint_completed: set[str] = set()
        self.checkpoint_signature = ""
        self.current_payload_received = False
        self.current_engine_error = ""
        self.current_done_payload: dict[str, object] | None = None
        self.cancelled = False
        self.mode = "validate"
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.finished.connect(self._process_finished)
        self.threshold_process = QProcess(self)
        self.threshold_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.threshold_process.readyReadStandardOutput.connect(self._read_threshold_output)
        self.threshold_process.finished.connect(self._threshold_process_finished)
        self.threshold_targets: list[dict[str, str]] = []
        self.threshold_profiles: list[dict[str, object]] = []
        self.threshold_output_directory = Path()
        self.threshold_rules: dict[str, object] = {}
        self.threshold_current_profile: dict[str, object] | None = None
        self.threshold_current_error = ""
        self.calibration_process: subprocess.Popen | None = None
        self.calibration_timer = QTimer(self)
        self.calibration_timer.setInterval(750)
        self.calibration_timer.timeout.connect(self._poll_calibration)
        self._build_ui()
        self._build_shortcuts()
        saved_project = Path(str(self.settings.get("project", "")))
        if saved_project.is_file():
            self._load_project(saved_project)

    def _defaults(self) -> dict[str, object]:
        return {
            "project": "",
            "qupath": str(DEFAULT_QUPATH),
            "cellpose_python": str(DEFAULT_CELLPOSE),
            "fiji": str(DEFAULT_FIJI),
            "channel_1_name": "NeuN",
            "channel_2_name": "c-Fos",
            "channel_3_name": "DAPI",
            "channel_1_threshold": "50",
            "channel_2_threshold": "50",
            "channel_3_threshold": "50",
            "channel_1": 1,
            "channel_2": 2,
            "channel_3": 3,
            "require_channel_3": False,
            "cellprob": "-0.5",
            "flow": "0.4",
            "cellpose_model": "cpsam",
            "reuse_detections": True,
            "hemisphere_mode": "Left and Right",
            "channel_1_start": "0",
            "channel_1_end": "200",
            "channel_1_step": "5",
            "channel_2_start": "0",
            "channel_2_end": "200",
            "channel_2_step": "5",
            "channel_3_start": "0",
            "channel_3_end": "200",
            "channel_3_step": "5",
            "total_change_limit": "3",
            "section_change_limit": "5",
            "stable_section_percent": "90",
            "minimum_stable_steps": "3",
            "threshold_objective": "Channel 1 positive + Channel 2 positive",
            "automatic_background_guard": True,
            "threshold_locked": False,
        }

    def _load_settings(self) -> dict[str, object]:
        settings = self._defaults()
        if not SETTINGS_FILE.is_file():
            return settings
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            settings["project"] = saved.get("project", settings["project"])
            names = saved.get("channel_names", {})
            for index in range(1, 4):
                settings[f"channel_{index}_name"] = names.get(str(index), settings[f"channel_{index}_name"])
            thresholds = saved.get("global_thresholds", {})
            for index in range(1, 4):
                settings[f"channel_{index}_threshold"] = str(thresholds.get(f"channel{index}", settings[f"channel_{index}_threshold"]))
            cellpose = saved.get("cellpose_parameters", {})
            settings["cellprob"] = str(cellpose.get("cellprob", settings["cellprob"]))
            settings["flow"] = str(cellpose.get("flow", settings["flow"]))
            settings["cellpose_model"] = str(cellpose.get("model", settings["cellpose_model"]))
            settings["hemisphere_mode"] = saved.get("hemisphere_mode", settings["hemisphere_mode"])
            settings["qupath"] = saved.get("qupath_path", settings["qupath"])
            settings["cellpose_python"] = saved.get("cellpose_python_path", settings["cellpose_python"])
            settings["fiji"] = saved.get("fiji_path", settings["fiji"])
            behavior = saved.get("analysis_behavior", {})
            if isinstance(behavior, dict):
                settings["require_channel_3"] = bool(behavior.get("require_channel_3", settings["require_channel_3"]))
                settings["reuse_detections"] = bool(behavior.get("reuse_detections", settings["reuse_detections"]))
            if isinstance(thresholds, dict):
                settings["threshold_locked"] = bool(thresholds.get("locked", False))
                settings["threshold_objective"] = thresholds.get("objective", settings["threshold_objective"])
                settings["automatic_background_guard"] = bool(
                    thresholds.get("automatic_background_guard", settings["automatic_background_guard"])
                )
                ranges = thresholds.get("ranges", {})
                for index in range(1, 4):
                    channel_range = ranges.get(f"channel{index}", {}) if isinstance(ranges, dict) else {}
                    for key in ("start", "end", "step"):
                        settings[f"channel_{index}_{key}"] = str(
                            channel_range.get(key, settings[f"channel_{index}_{key}"])
                        )
                stability = thresholds.get("stability", {})
                if isinstance(stability, dict):
                    for key in ("total_change_limit", "section_change_limit", "stable_section_percent", "minimum_stable_steps"):
                        settings[key] = str(stability.get(key, settings[key]))
        except (OSError, ValueError, TypeError):
            pass
        return settings

    def _save_settings(self) -> None:
        data: dict[str, object] = {}
        if SETTINGS_FILE.is_file():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                data = {}
        data.update({
            "project": str(self.settings["project"]),
            "qupath_path": str(self.settings["qupath"]),
            "cellpose_python_path": str(self.settings["cellpose_python"]),
            "fiji_path": str(self.settings["fiji"]),
            "channel_names": {str(i): str(self.settings[f"channel_{i}_name"]) for i in range(1, 4)},
            "hemisphere_mode": str(self.settings["hemisphere_mode"]),
            "analysis_behavior": {
                "require_channel_3": bool(self.settings["require_channel_3"]),
                "reuse_detections": bool(self.settings["reuse_detections"]),
            },
            "global_thresholds": {
                **{f"channel{i}": str(self.settings[f"channel_{i}_threshold"]) for i in range(1, 4)},
                "locked": bool(self.settings["threshold_locked"]),
                "objective": str(self.settings["threshold_objective"]),
                "automatic_background_guard": bool(self.settings["automatic_background_guard"]),
                "ranges": {
                    f"channel{i}": {
                        key: str(self.settings[f"channel_{i}_{key}"])
                        for key in ("start", "end", "step")
                    }
                    for i in range(1, 4)
                },
                "stability": {
                    key: str(self.settings[key])
                    for key in ("total_change_limit", "section_change_limit", "stable_section_percent", "minimum_stable_steps")
                },
            },
            "cellpose_parameters": {
                "model": str(self.settings["cellpose_model"]),
                "cellprob": str(self.settings["cellprob"]),
                "flow": str(self.settings["flow"]),
            },
        })
        SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(272)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 18)
        brand = QLabel("Brain Cell Counter")
        brand.setObjectName("brand")
        side.addWidget(brand)
        version = QLabel(f"Scientific workflow  •  {APP_VERSION_LABEL}")
        version.setObjectName("version")
        side.addWidget(version)
        side.addSpacing(22)
        self.nav_buttons: list[QPushButton] = []
        for index, text in enumerate(("Start", "Analysis", "Thresholds", "Results")):
            button = _button(text)
            button.setProperty("nav", True)
            button.clicked.connect(lambda _checked=False, page=index: self._show_page(page))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()
        settings_button = _button("Settings")
        settings_button.setProperty("nav", True)
        settings_button.clicked.connect(self._show_settings)
        side.addWidget(settings_button)
        help_button = _button("Help && methods")
        help_button.setProperty("nav", True)
        help_button.clicked.connect(self._show_help)
        side.addWidget(help_button)
        outer.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 22, 28, 20)
        content_layout.setSpacing(14)
        self.context = QLabel("No QuPath project selected")
        self.context.setProperty("muted", True)
        content_layout.addWidget(self.context)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._start_page())
        self.pages.addWidget(self._analysis_page())
        self.pages.addWidget(self._threshold_page())
        self.pages.addWidget(self._results_page())
        content_layout.addWidget(self.pages, 1)
        content_layout.addWidget(self._run_bar())
        outer.addWidget(content, 1)
        self._show_page(0, animate=False)

    def _scroll_page(self, title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("pageBody")
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: #FFFFFF; }")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 4, 14, 16)
        layout.setSpacing(14)
        heading = QLabel(title)
        heading.setProperty("heading", True)
        layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setProperty("muted", True)
        description.setWordWrap(True)
        layout.addWidget(description)
        scroll.setWidget(body)
        return scroll, layout

    def _start_page(self) -> QWidget:
        page, layout = self._scroll_page("Start", "Select one QuPath project, then prepare it in the order shown below.")
        project_card, card = _card("QuPath project", "Choose project.qpproj. The app reads entries without modifying the project.")
        row = QHBoxLayout()
        self.project_field = QLineEdit()
        self.project_field.setReadOnly(True)
        row.addWidget(self.project_field, 1)
        browse = _button("Choose project", primary=True)
        browse.clicked.connect(self._browse_project)
        row.addWidget(browse)
        card.addLayout(row)
        self.project_status = QLabel("Waiting for a project")
        self.project_status.setProperty("muted", True)
        card.addWidget(self.project_status)
        layout.addWidget(project_card)
        preparation = QHBoxLayout()
        for title, text, action, callback in (
            ("1. Calibration", "Human-confirm scale bars before writing any calibration.", "Open calibration", self._open_calibration),
            ("2. Atlas", "Import each entry's own exported ABBA registration.", "Import ABBA", self._start_abba),
            ("3. File links", "Report missing source images before processing.", "Run preflight", self._preflight),
        ):
            frame, box = _card(title, text)
            button = _button(action)
            button.clicked.connect(callback)
            box.addWidget(button)
            if callback == self._open_calibration:
                self.calibration_button = button
            preparation.addWidget(frame, 1)
        layout.addLayout(preparation)
        layout.addStretch()
        return page

    def _field(self, value: object, tip: str) -> QLineEdit:
        field = QLineEdit(str(value))
        field.setToolTip(tip)
        return field

    def _analysis_page(self) -> QWidget:
        page, layout = self._scroll_page("Analysis", "These settings are shared by every entry in the sequential batch.")
        names_card, names = _card("1. Channel names", "Names affect labels and reports; channel numbers remain 1, 2, and 3.")
        names_form = QFormLayout()
        self.name_fields = []
        for index, color in ((1, "RED"), (2, "GREEN"), (3, "BLUE")):
            field = self._field(self.settings[f"channel_{index}_name"], "Editable display name saved for future sessions.")
            self.name_fields.append(field)
            names_form.addRow(f"Channel {index} ({color})", field)
        names.addLayout(names_form)
        layout.addWidget(names_card)
        threshold_card, thresholds = _card("2. Positive-intensity thresholds", "Use validated dataset-wide values; do not optimize each image independently.")
        threshold_form = QFormLayout()
        self.threshold_fields = []
        for index in range(1, 4):
            field = self._field(self.settings[f"channel_{index}_threshold"], "Mean-intensity cutoff for this channel.")
            self.threshold_fields.append(field)
            threshold_form.addRow(f"Channel {index} threshold", field)
        self.require_channel_3 = QCheckBox("Require Channel 3 intensity threshold")
        self.require_channel_3.setChecked(bool(self.settings["require_channel_3"]))
        threshold_form.addRow("", self.require_channel_3)
        thresholds.addLayout(threshold_form)
        layout.addWidget(threshold_card)
        behavior_card, behavior = _card("3. Analysis behavior")
        behavior_form = QFormLayout()
        self.reuse = QCheckBox("Reuse compatible existing detections")
        self.reuse.setChecked(bool(self.settings["reuse_detections"]))
        self.hemisphere = QComboBox()
        self.hemisphere.addItems(["Left and Right", "Do not split"])
        self.hemisphere.setCurrentText(str(self.settings["hemisphere_mode"]))
        self.cellprob = self._field(self.settings["cellprob"], "Lower values usually create more masks.")
        self.flow = self._field(self.settings["flow"], "Lower values reject more questionable masks.")
        self.cellpose_model = QComboBox()
        self.cellpose_model.addItems(list(SUPPORTED_CELLPOSE_MODELS))
        self.cellpose_model.setCurrentText(str(self.settings["cellpose_model"]))
        self.cellpose_model.setToolTip(
            "cpsam is the validated baseline. cpsam_v2 reduces spurious masks in low-contrast regions; validate it on representative ROIs before batch use."
        )
        behavior_form.addRow("Detections", self.reuse)
        behavior_form.addRow("Hemisphere", self.hemisphere)
        behavior_form.addRow("Cellpose model", self.cellpose_model)
        behavior_form.addRow("Cellprob threshold", self.cellprob)
        behavior_form.addRow("Flow threshold", self.flow)
        behavior.addLayout(behavior_form)
        suggest = _button("Auto suggest Cellpose")
        suggest.setToolTip("Test 15 Cellprob/Flow combinations on representative crops without saving the project.")
        suggest.clicked.connect(self._start_cellpose_suggestion)
        behavior.addWidget(suggest, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(behavior_card)
        layout.addStretch()
        return page

    def _threshold_page(self) -> QWidget:
        page, layout = self._scroll_page("Thresholds", "Find one stable dataset-wide threshold per channel or channel pair.")
        card, box = _card(
            "Global plateau finder",
            "Scan existing object intensities across the project. Cellpose does not run again, and the optimizer never changes detections.",
        )
        form = QFormLayout()
        self.threshold_scope = QComboBox()
        self.threshold_scope.addItems(["Current project — all entries", "Selected entry only — test"])
        self.threshold_objective = QComboBox()
        self.threshold_objective.addItems([*THRESHOLD_PAIR_TYPES, INDIVIDUAL_THRESHOLD_TYPE])
        self.threshold_objective.setCurrentText(str(self.settings["threshold_objective"]))
        self.threshold_entry = QComboBox()
        form.addRow("Sections", self.threshold_scope)
        form.addRow("Test entry", self.threshold_entry)
        form.addRow("Threshold type", self.threshold_objective)
        self.background_guard = QCheckBox("Automatic background/positive separation guard")
        self.background_guard.setChecked(bool(self.settings["automatic_background_guard"]))
        self.background_guard.setToolTip("Rejects saturated low-threshold plateaus using the dataset-wide intensity distribution.")
        form.addRow("", self.background_guard)
        self.threshold_lock = QCheckBox("Lock optimized thresholds for every entry")
        self.threshold_lock.setChecked(bool(self.settings["threshold_locked"]))
        self.threshold_lock.toggled.connect(self._threshold_lock_changed)
        form.addRow("", self.threshold_lock)
        box.addLayout(form)
        layout.addWidget(card)

        advanced_card, advanced = _card(
            "Candidate ranges and plateau rules",
            "Defaults are suitable for an initial scan. A plateau requires several consecutive stable threshold steps.",
        )
        grid = QGridLayout()
        grid.addWidget(QLabel("Channel"), 0, 0)
        for column, label in enumerate(("Start", "End", "Step"), 1):
            grid.addWidget(QLabel(label), 0, column)
        self.threshold_range_fields: dict[tuple[int, str], QLineEdit] = {}
        for row, index in enumerate(range(1, 4), 1):
            grid.addWidget(QLabel(f"Channel {index}"), row, 0)
            for column, key in enumerate(("start", "end", "step"), 1):
                field = self._field(self.settings[f"channel_{index}_{key}"], f"Channel {index} candidate threshold {key}.")
                self.threshold_range_fields[(index, key)] = field
                grid.addWidget(field, row, column)
        self.threshold_rule_fields: dict[str, QLineEdit] = {}
        rules = (
            ("Total change < %", "total_change_limit"),
            ("Section change < %", "section_change_limit"),
            ("Stable sections ≥ %", "stable_section_percent"),
            ("Consecutive steps ≥", "minimum_stable_steps"),
        )
        for offset, (label, key) in enumerate(rules, 5):
            grid.addWidget(QLabel(label), offset, 0)
            field = self._field(self.settings[key], label)
            self.threshold_rule_fields[key] = field
            grid.addWidget(field, offset, 1)
        advanced.addLayout(grid)
        run_row = QHBoxLayout()
        self.threshold_status = QLabel("Ready to scan existing detections")
        self.threshold_status.setProperty("muted", True)
        run_row.addWidget(self.threshold_status, 1)
        self.threshold_run_button = _button("Find global threshold", accent=True)
        self.threshold_run_button.clicked.connect(self._start_threshold_optimization)
        run_row.addWidget(self.threshold_run_button)
        advanced.addLayout(run_row)
        layout.addWidget(advanced_card)
        layout.addStretch()
        self._update_threshold_lock_state()
        return page

    def _results_page(self) -> QWidget:
        # Results owns internal table and image scroll areas, so nesting it in
        # another QScrollArea can shift or blank the page on Windows.
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 14, 16)
        layout.setSpacing(14)
        heading = QLabel("Results")
        heading.setProperty("heading", True)
        layout.addWidget(heading)
        description = QLabel("Open versioned CSV and atlas-overlay outputs without modifying master files.")
        description.setProperty("muted", True)
        description.setWordWrap(True)
        layout.addWidget(description)
        card, box = _card("Project results", "Choose columns, preview every Allen region, export a compact CSV, or inspect saved detections in QuPath.")
        self.results_widget = QtResultsWidget(self._open_result_in_qupath)
        box.addWidget(self.results_widget)
        layout.addWidget(card, 1)
        return page

    def _run_bar(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("card", True)
        layout = QVBoxLayout(frame)
        top = QHBoxLayout()
        self.state_label = QLabel("READY")
        self.state_label.setProperty("status", True)
        top.addWidget(self.state_label)
        self.progress_label = QLabel("Choose a project to begin")
        self.progress_label.setProperty("muted", True)
        top.addWidget(self.progress_label, 1)
        self.validate_button = _button("Validate")
        self.validate_button.clicked.connect(self._start_validation)
        top.addWidget(self.validate_button)
        self.analyze_button = _button("Analyze", accent=True)
        self.analyze_button.clicked.connect(self._choose_analysis)
        top.addWidget(self.analyze_button)
        self.cancel_button = _button("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        top.addWidget(self.cancel_button)
        log_button = _button("Run log")
        log_button.clicked.connect(self._show_log)
        top.addWidget(log_button)
        layout.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log_dialog = QDialog(self)
        self.log_dialog.setWindowTitle(f"Brain Cell Counter {APP_VERSION_LABEL} — Run log")
        self.log_dialog.resize(980, 600)
        log_layout = QVBoxLayout(self.log_dialog)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Cascadia Mono", 9))
        log_layout.addWidget(self.log)
        close = _button("Close")
        close.clicked.connect(self.log_dialog.hide)
        log_layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        return frame

    def _build_shortcuts(self) -> None:
        for sequence, callback in (("Ctrl+O", self._browse_project), ("Ctrl+L", self._show_log), ("F1", self._show_help)):
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(callback)
            self.addAction(action)

    def _show_page(self, index: int, animate: bool = True) -> None:
        """Switch pages without opacity effects that can blank complex widgets."""
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setProperty("selected", button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)
        if index == 3:
            QTimer.singleShot(0, self.results_widget.ensure_loaded)

    def _browse_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose QuPath project", str(self.settings.get("project", "")), "QuPath project (*.qpproj)")
        if path:
            self._load_project(Path(path))

    def _load_project(self, path: Path) -> None:
        try:
            self.summary = read_project(path)
        except Exception as exc:
            QMessageBox.critical(self, "Project", f"Could not read the QuPath project:\n{exc}")
            return
        self.settings["project"] = str(self.summary.path)
        self.project_field.setText(str(self.summary.path))
        count = len(self.summary.entries)
        ready = self.summary.analysis_ready_count
        self.project_status.setText(f"{count} image entries  •  {ready} ready with source + ABBA + ontology")
        self.context.setText(f"{self.summary.path.name}  •  {count} entries  •  {ready} analysis-ready")
        self.progress_label.setText("Project loaded — run Validate before Analyze")
        self.threshold_entry.clear()
        self.threshold_entry.addItems([entry.name for entry in self.summary.entries])
        self.results_widget.set_project(self.summary.path)
        self._save_settings()

    def _collect_settings(self) -> None:
        for index, field in enumerate(self.name_fields, 1):
            self.settings[f"channel_{index}_name"] = field.text().strip()
        for index, field in enumerate(self.threshold_fields, 1):
            self.settings[f"channel_{index}_threshold"] = field.text().strip()
        self.settings["require_channel_3"] = self.require_channel_3.isChecked()
        self.settings["reuse_detections"] = self.reuse.isChecked()
        self.settings["hemisphere_mode"] = self.hemisphere.currentText()
        self.settings["cellpose_model"] = self.cellpose_model.currentText()
        self.settings["cellprob"] = self.cellprob.text().strip()
        self.settings["flow"] = self.flow.text().strip()
        for (index, key), field in self.threshold_range_fields.items():
            self.settings[f"channel_{index}_{key}"] = field.text().strip()
        for key, field in self.threshold_rule_fields.items():
            self.settings[key] = field.text().strip()
        self.settings["threshold_objective"] = self.threshold_objective.currentText()
        pair = THRESHOLD_PAIR_TYPES.get(self.threshold_objective.currentText())
        self.settings["threshold_pair"] = "-".join(pair) if pair else "individual"
        self.settings["automatic_background_guard"] = self.background_guard.isChecked()
        self.settings["threshold_locked"] = self.threshold_lock.isChecked()
        self._save_settings()

    def _threshold_lock_changed(self, locked: bool) -> None:
        """Keep optimized thresholds immutable until the user unlocks them."""
        self.settings["threshold_locked"] = bool(locked)
        self._update_threshold_lock_state()
        self._save_settings()

    def _update_threshold_lock_state(self) -> None:
        """Reflect the global lock in the three analysis threshold fields."""
        locked = bool(self.threshold_lock.isChecked())
        for field in self.threshold_fields:
            field.setReadOnly(locked)
            field.setToolTip(
                "Unlock thresholds on the Thresholds page before editing."
                if locked
                else "Mean-intensity cutoff for this channel."
            )

    def _show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings["qupath"] = dialog.qupath.text().strip()
            self.settings["cellpose_python"] = dialog.cellpose.text().strip()
            self.settings["fiji"] = dialog.fiji.text().strip()
            self._save_settings()

    def _show_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Help & methods")
        dialog.resize(900, 700)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(f"""# Brain Cell Counter {APP_VERSION_LABEL}

## Standard workflow

1. Register every section with ABBA / DeepSlice and export it to the QuPath project.
2. Choose `project.qpproj` on **Start**.
3. Use **Calibration** only when pixel calibration is missing or incorrect; every scale bar requires human confirmation.
4. Run **Import ABBA** when exported atlas annotations have not been loaded.
5. Run **Preflight**, close the QuPath desktop, then run **Validate**.
6. Review Channel 1/2/3 names, thresholds, Cellpose model, Cellprob, Flow, reuse, and hemisphere mode.
7. Click **Analyze**, choose entries, and confirm. Entries run sequentially from top to bottom.
8. Review regional CSV files, atlas overlays, the run log, and saved detections in QuPath.

## Segmentation and classification

Cellpose-SAM creates DAPI-defined objects. `cpsam` is the validated baseline; `cpsam_v2` must be compared on representative ROIs before batch use. Changing the selection does not relabel reused legacy detections. Channel 1 and Channel 2 positivity is then classified from the mean intensity inside each object. Counts are object-based co-expression, not pixel-level colocalization. Lower Cellprob generally creates more masks; a lower Flow threshold rejects more questionable masks.

## Global threshold algorithm

For every candidate threshold, the optimizer counts objects in every section. A step is stable only when total count change is below the configured total limit and the required percentage of individual sections is also stable. A plateau requires several consecutive stable steps. The proposal is the highest threshold inside the stable plateau before a clear loss of detections. Channels are optimized independently unless a positive-pair objective is selected.

The automatic background guard estimates a negative/positive split from the dataset-wide intensity distribution and rejects saturated low-threshold plateaus. All proposals still require controls, intensity distributions, and visual inspection. Never choose a threshold only because it produces the most cells.

## Safety and reproducibility

- Source TIFF files are never overwritten.
- Only one QuPath/Cellpose process runs at a time.
- Analysis checkpoints are accepted only when the project and exact parameters match.
- Physical area and density remain blank when calibration is unknown.
- Close the QuPath desktop before an operation that writes the project.

Calibration opens as a dedicated human-review window. Threshold optimization, compact result export, project-link repair, Cellpose suggestion, and **Open in QuPath** are integrated directly into this interface. The classic main window is no longer required for the standard workflow.
""")
        layout.addWidget(browser)
        close = _button("Close", primary=True)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _preflight(self) -> None:
        if self.summary is None:
            QMessageBox.warning(self, "Preflight", "Choose a QuPath project first.")
            return
        missing = [entry.name for entry in self.summary.entries if not entry.source_exists]
        if not missing:
            QMessageBox.information(self, "Preflight", "All project source images are accessible.")
            return
        choice = QMessageBox.question(
            self,
            "Preflight",
            "Missing source images:\n\n- " + "\n- ".join(missing)
            + "\n\nSearch a folder recursively for unique files with matching names?",
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose the folder containing moved source images")
        if not folder:
            return
        try:
            project_data = json.loads(self.summary.path.read_text(encoding="utf-8-sig"))
            proposals: list[tuple[dict[str, object], Path]] = []
            ambiguous: list[str] = []
            unresolved: list[str] = []
            for entry in project_data.get("images", []):
                image_name = str(entry.get("imageName") or "Unnamed entry")
                if image_name not in missing:
                    continue
                container = self._find_uri_container(entry.get("serverBuilder", {}))
                if container is None:
                    unresolved.append(image_name)
                    continue
                old_uri = str(container[0][container[1]])
                old_path = file_uri_to_path(old_uri)
                source_name = old_path.name if old_path is not None else image_name
                matches = [path for path in Path(folder).rglob(source_name) if path.is_file()]
                if len(matches) == 1:
                    proposals.append((container[0], matches[0].resolve()))
                elif len(matches) > 1:
                    ambiguous.append(image_name)
                else:
                    unresolved.append(image_name)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "Preflight", f"Could not inspect the project:\n{exc}")
            return
        details = [f"{path.name} → {path}" for _container, path in proposals]
        if ambiguous:
            details.append("Multiple matches, unchanged: " + ", ".join(ambiguous))
        if unresolved:
            details.append("Not found: " + ", ".join(unresolved))
        if not proposals:
            QMessageBox.warning(self, "Preflight", "No unique replacement files were found.\n\n" + "\n".join(details))
            return
        confirm = QMessageBox.question(
            self,
            "Apply repaired image links",
            f"Apply {len(proposals)} unique replacement link(s)?\n\n"
            + "\n".join(details)
            + "\n\nA timestamped project backup will be created first.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self.summary.path.with_name(f"{self.summary.path.name}.preflight-backup-{timestamp}.bak")
            shutil.copy2(self.summary.path, backup)
            for container, replacement in proposals:
                container["uri"] = replacement.as_uri()
            temporary = self.summary.path.with_name(self.summary.path.name + ".preflight.tmp")
            temporary.write_text(json.dumps(project_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.summary.path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "Preflight", f"Could not repair project links:\n{exc}")
            return
        self._append_log(f"PREFLIGHT repaired {len(proposals)} source link(s). Backup: {backup}")
        self._load_project(self.summary.path)
        QMessageBox.information(self, "Preflight", f"Repaired {len(proposals)} link(s).\n\nBackup:\n{backup}")

    @classmethod
    def _find_uri_container(cls, value: object) -> tuple[dict[str, object], str] | None:
        """Find the mutable mapping that owns a project image file URI."""
        if isinstance(value, dict):
            uri = value.get("uri")
            if isinstance(uri, str) and uri.lower().startswith("file:"):
                return value, "uri"
            for nested in value.values():
                found = cls._find_uri_container(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = cls._find_uri_container(nested)
                if found is not None:
                    return found
        return None

    def _start_abba(self) -> None:
        if self.summary is None:
            QMessageBox.warning(self, "Import ABBA", "Choose a QuPath project first.")
            return
        names = [entry.name for entry in self.summary.entries if entry.abba_ready and self.summary.ontology_found]
        if not names:
            QMessageBox.warning(self, "Import ABBA", "No entry has a complete exported ABBA registration.")
            return
        if QMessageBox.question(self, "Import ABBA", f"Import and save atlas annotations for {len(names)} entries sequentially?") == QMessageBox.StandardButton.Yes:
            self._start_queue("apply-atlas", names)

    def _open_calibration(self) -> None:
        """Open only the dedicated calibration wizard, never the classic shell."""
        if self.summary is None:
            QMessageBox.warning(self, "Calibration", "Choose a QuPath project first.")
            return
        qupath = Path(str(self.settings["qupath"]))
        if not qupath.is_file():
            QMessageBox.warning(self, "Calibration", "Choose the QuPath Console executable in Settings.")
            return
        if self.process.state() != QProcess.ProcessState.NotRunning or self.threshold_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "Calibration", "Wait for the active project operation to finish.")
            return
        if self.calibration_process is not None and self.calibration_process.poll() is None:
            QMessageBox.information(self, "Calibration", "The Calibration window is already open.")
            return
        executable = Path(sys.executable).with_name("pythonw.exe")
        if not executable.is_file():
            executable = Path(sys.executable)
        command = [
            str(executable),
            "-m",
            "brain_cell_counter.calibration_runner",
            "--project",
            str(self.summary.path),
            "--qupath",
            str(qupath),
            "--fiji",
            str(self.settings.get("fiji", "")),
        ]
        self.calibration_process = subprocess.Popen(command, cwd=str(ROOT))
        self._append_log("Opened the dedicated calibration workflow.")
        self.validate_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
        self.threshold_run_button.setEnabled(False)
        self.calibration_button.setEnabled(False)
        self.results_widget.set_busy(True)
        self.progress_label.setText("Calibration window is open")
        self.calibration_timer.start()

    def _poll_calibration(self) -> None:
        """Restore project controls after the dedicated calibration process closes."""
        if self.calibration_process is not None and self.calibration_process.poll() is None:
            return
        self.calibration_timer.stop()
        self.calibration_process = None
        self.validate_button.setEnabled(True)
        self.analyze_button.setEnabled(True)
        self.threshold_run_button.setEnabled(True)
        self.calibration_button.setEnabled(True)
        self.results_widget.set_busy(False)
        self.progress_label.setText("Calibration closed — run Preflight and Validate before analysis")
        if self.summary is not None:
            self._load_project(self.summary.path)

    def _start_cellpose_suggestion(self) -> None:
        """Run the existing non-saving Cellpose parameter heuristic from Qt."""
        if self.summary is None:
            QMessageBox.warning(self, "Cellpose suggestion", "Choose a QuPath project first.")
            return
        image, accepted = QInputDialog.getItem(
            self,
            "Cellpose suggestion",
            "Representative image entry",
            [entry.name for entry in self.summary.entries],
            0,
            False,
        )
        if not accepted or not image:
            return
        if QMessageBox.question(
            self,
            "Cellpose suggestion",
            "Test 15 Cellprob/Flow combinations on three representative crops?\n\n"
            "This does not save the QuPath entry and the result still requires visual mask review.",
        ) == QMessageBox.StandardButton.Yes:
            self._start_queue("cellpose-calibrate", [image])

    def _start_validation(self) -> None:
        if self.summary is not None:
            self._start_queue("validate", [entry.name for entry in self.summary.entries])
        else:
            QMessageBox.warning(self, "Validate", "Choose a QuPath project first.")

    def _start_threshold_optimization(self) -> None:
        """Scan every selected entry sequentially and calculate global plateaus."""
        if self.summary is None:
            QMessageBox.warning(self, "Global threshold", "Choose a QuPath project first.")
            return
        if (
            self.process.state() != QProcess.ProcessState.NotRunning
            or self.threshold_process.state() != QProcess.ProcessState.NotRunning
            or (self.calibration_process is not None and self.calibration_process.poll() is None)
        ):
            QMessageBox.warning(self, "Global threshold", "Wait for the active project operation to finish.")
            return
        self._collect_settings()
        try:
            arguments = analysis_arguments(self.settings, "threshold-profile")
            total_limit = float(self.settings["total_change_limit"])
            section_limit = float(self.settings["section_change_limit"])
            stable_percent = float(self.settings["stable_section_percent"])
            minimum_steps = int(self.settings["minimum_stable_steps"])
            for index in range(1, 4):
                start = float(self.settings[f"channel_{index}_start"])
                end = float(self.settings[f"channel_{index}_end"])
                step = float(self.settings[f"channel_{index}_step"])
                if step <= 0 or end < start:
                    raise ValueError(f"Channel {index} range requires End ≥ Start and Step > 0.")
            if total_limit <= 0 or section_limit <= 0 or not 0 < stable_percent <= 100 or minimum_steps < 1:
                raise ValueError("Plateau limits must be positive; stable sections must be between 0 and 100%.")
            objective_label = self.threshold_objective.currentText()
            pair_keys = THRESHOLD_PAIR_TYPES.get(objective_label)
            rules: dict[str, object] = {
                "total_change_limit": total_limit,
                "section_change_limit": section_limit,
                "required_stable_fraction": stable_percent / 100.0,
                "minimum_consecutive_steps": minimum_steps,
                "objective": "maximize_pair_overlap" if pair_keys else "independent_plateaus",
                "pair_keys": pair_keys,
                "automatic_background_guard": self.background_guard.isChecked(),
            }
        except (TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Global threshold", str(exc))
            return

        entries = [entry.name for entry in self.summary.entries]
        if self.threshold_scope.currentText().startswith("Selected"):
            selected = self.threshold_entry.currentText()
            entries = [selected] if selected else []
        if not entries:
            QMessageBox.warning(self, "Global threshold", "No image entries were selected.")
            return
        warning = "\n\nA one-entry scan is a test and must not be locked as a batch threshold." if len(entries) == 1 else ""
        if QMessageBox.question(
            self,
            "Find global threshold",
            f"Scan existing intensity measurements in {len(entries)} image entry(s)?\n"
            "Cellpose will not run again. Every entry must already contain compatible detections."
            f"\n\nObjective: {objective_label}.{warning}",
        ) != QMessageBox.StandardButton.Yes:
            return

        self.threshold_targets = [
            {
                "project": str(self.summary.path),
                "image": name,
                "section_id": f"{self.summary.path.parent} :: {name}",
            }
            for name in entries
        ]
        self.threshold_arguments = arguments
        self.threshold_rules = rules
        self.threshold_output_directory = self.summary.path.parent / "brain_cell_counter_threshold_results"
        self.threshold_profiles = []
        self.threshold_status.setText(f"Scanning 0/{len(entries)} entries")
        self._append_log("=" * 72)
        self._append_log(f"Global threshold scan started: {len(entries)} entries; objective: {objective_label}")
        self._set_running(True)
        self._start_next_threshold()

    def _start_next_threshold(self) -> None:
        """Start the next non-saving threshold-profile process."""
        if not self.threshold_targets:
            self._finish_threshold_optimization()
            return
        target = self.threshold_targets[0]
        self.threshold_current_profile = None
        self.threshold_current_error = ""
        command = qupath_command(
            Path(str(self.settings["qupath"])),
            Path(target["project"]),
            target["image"],
            qupath_script_path(ENGINE_FILENAME),
            self.threshold_arguments,
            save=False,
        )
        index = len(self.threshold_profiles) + 1
        total = len(self.threshold_profiles) + len(self.threshold_targets)
        self.threshold_status.setText(f"Scanning {index}/{total}: {target['image']}")
        self._append_log(f"[{index}/{total}] THRESHOLD PROFILE: {target['image']}")
        self.threshold_process.setWorkingDirectory(str(ROOT))
        self.threshold_process.start(command[0], command[1:])

    def _read_threshold_output(self) -> None:
        """Collect structured profile and progress events from QuPath."""
        text = bytes(self.threshold_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line and not line.startswith("WARNING: Unknown module:"):
                self._append_log(line)
            if not line.startswith("BCC_EVENT "):
                continue
            try:
                event = json.loads(line[len("BCC_EVENT "):])
            except (TypeError, ValueError):
                continue
            kind = event.get("kind")
            payload = event.get("payload")
            if kind == "threshold_profile" and isinstance(payload, dict):
                self.threshold_current_profile = payload
            elif kind == "error":
                self.threshold_current_error = str(payload)
            elif kind == "progress" and isinstance(payload, dict):
                current = len(self.threshold_profiles)
                total = current + len(self.threshold_targets)
                overall = (current + float(payload.get("percent", 0)) / 100.0) / total * 100.0
                self.progress.setValue(round(overall))

    def _threshold_process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        """Accept one profile or stop safely with an actionable error."""
        if not self.threshold_targets:
            if self.cancelled:
                self._set_running(False)
                self.state_label.setText("CANCELLED")
                self.threshold_status.setText("Threshold scan cancelled")
            return
        target = self.threshold_targets.pop(0)
        if self.cancelled:
            self.threshold_targets.clear()
            self._set_running(False)
            self.threshold_status.setText("Threshold scan cancelled")
            return
        if exit_code != 0 or self.threshold_current_error or self.threshold_current_profile is None:
            reason = self.threshold_current_error or f"QuPath exited with code {exit_code} or returned no threshold profile."
            self._append_log(f"THRESHOLD SCAN FAILED: {target['image']} — {reason}")
            self.threshold_targets.clear()
            self._set_running(False)
            self.state_label.setText("ATTENTION")
            self.threshold_status.setText("Scan failed — review Run log")
            QMessageBox.critical(self, "Global threshold", f"Threshold scan failed for {target['image']}:\n\n{reason}")
            return
        profile = dict(self.threshold_current_profile)
        profile["section_id"] = target["section_id"]
        profile["project"] = target["project"]
        self.threshold_profiles.append(profile)
        self._start_next_threshold()

    def _finish_threshold_optimization(self) -> None:
        """Write reports and optionally apply all valid recommended thresholds."""
        try:
            result = calculate_global_thresholds(self.threshold_profiles, **self.threshold_rules)
            result["scope"] = self.threshold_scope.currentText()
            reports = write_threshold_reports(result, self.threshold_output_directory)
            result["reports"] = reports
        except Exception as exc:
            self._set_running(False)
            self.state_label.setText("ATTENTION")
            self.threshold_status.setText("Optimization failed — review Run log")
            self._append_log(f"THRESHOLD OPTIMIZATION FAILED: {exc}")
            QMessageBox.critical(self, "Global threshold", str(exc))
            return
        channels = result.get("channels", [])
        summaries = []
        complete = True
        for channel in channels:
            selected = channel.get("selected_threshold")
            complete = complete and selected is not None
            value = "None" if selected is None else f"{float(selected):g}"
            summaries.append(f"{channel.get('name', channel.get('key'))}: {value}")
        self._set_running(False)
        self.progress.setValue(100)
        self.state_label.setText("COMPLETE" if complete else "ATTENTION")
        self.threshold_status.setText("Threshold reports created")
        self._append_log("GLOBAL THRESHOLD RESULT: " + "; ".join(summaries))
        self._append_log("Reports: " + json.dumps(reports, ensure_ascii=False))
        message = "Optimization completed:\n\n" + "\n".join(summaries)
        if not complete:
            QMessageBox.warning(self, "Global threshold", message + "\n\nAt least one channel has no valid plateau. Extend the range or review detections.")
            return
        if QMessageBox.question(
            self,
            "Apply global thresholds",
            message + "\n\nApply these values and lock them for every image entry?",
        ) != QMessageBox.StandardButton.Yes:
            return
        by_key = {str(channel["key"]): channel for channel in channels}
        for index in range(1, 4):
            value = float(by_key[f"channel{index}"]["selected_threshold"])
            self.threshold_fields[index - 1].setText(f"{value:g}")
            self.settings[f"channel_{index}_threshold"] = f"{value:g}"
        self.require_channel_3.setChecked(True)
        self.settings["require_channel_3"] = True
        self.threshold_lock.setChecked(True)
        self.settings["threshold_locked"] = True
        self._save_settings()
        self._append_log("Optimized global thresholds applied and locked.")

    def _choose_analysis(self) -> None:
        if self.summary is None:
            QMessageBox.warning(self, "Analyze", "Choose a QuPath project first.")
            return
        dialog = EntryDialog([entry.name for entry in self.summary.entries], self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected():
            return
        selected = dialog.selected()
        missing_sources = [
            entry.name
            for entry in self.summary.entries
            if entry.name in selected and not entry.source_exists
        ]
        if missing_sources:
            QMessageBox.critical(
                self,
                "Preflight required",
                "These selected entries have missing source images:\n\n- "
                + "\n- ".join(missing_sources)
                + "\n\nRun Preflight before analysis.",
            )
            return
        self._collect_settings()
        try:
            arguments = analysis_arguments(self.settings, "analyze")
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid settings", str(exc))
            return
        signature = analysis_signature(self.summary.path, arguments)
        completed = read_checkpoint(checkpoint_path(self.summary.path), signature)
        resumable = [name for name in selected if name in completed]
        if resumable:
            choice = QMessageBox.question(
                self,
                "Resume analysis",
                f"A matching checkpoint contains {len(resumable)} selected entries.\n\n"
                "Choose Yes to skip them, or No to rerun every selected entry.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Yes:
                selected = [name for name in selected if name not in completed]
            else:
                completed.difference_update(selected)
        if not selected:
            QMessageBox.information(self, "Resume analysis", "All selected entries are already complete for these parameters.")
            return
        if QMessageBox.question(self, "Analyze", "Analyze and save the selected entries sequentially? Close the QuPath desktop first.") == QMessageBox.StandardButton.Yes:
            self.checkpoint_completed = completed
            self.checkpoint_signature = signature
            self._start_queue("analyze", selected)

    def _start_queue(self, mode: str, names: list[str]) -> None:
        if (
            self.process.state() != QProcess.ProcessState.NotRunning
            or self.threshold_process.state() != QProcess.ProcessState.NotRunning
            or (self.calibration_process is not None and self.calibration_process.poll() is None)
        ):
            return
        self._collect_settings()
        try:
            analysis_arguments(self.settings, mode)
            qupath = Path(str(self.settings["qupath"]))
            if not qupath.is_file():
                raise ValueError("Choose an existing QuPath Console executable in Settings.")
        except (TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Invalid settings", str(exc))
            return
        self.mode = mode
        self.queue = list(names)
        self.total = len(names)
        self.completed = 0
        self.failed = []
        self.cancelled = False
        self.log.clear()
        self._append_log(f"{mode.upper()} started: {self.total} image entries")
        self._set_running(True)
        self._start_next()

    def _start_next(self) -> None:
        if not self.queue:
            self._finish_queue()
            return
        image = self.queue[0]
        self.current_payload_received = False
        self.current_engine_error = ""
        try:
            arguments = analysis_arguments(self.settings, self.mode)
        except ValueError as exc:
            self.failed.append(f"{image}: {exc}")
            self.queue.pop(0)
            self._start_next()
            return
        command = qupath_command(
            Path(str(self.settings["qupath"])),
            self.summary.path,
            image,
            qupath_script_path(ENGINE_FILENAME),
            arguments,
            save=self.mode in {"analyze", "apply-atlas"},
        )
        index = self.total - len(self.queue) + 1
        self._append_log(f"[{index}/{self.total}] Starting {self.mode.upper()}: {image}")
        self.progress_label.setText(f"Image {index}/{self.total} — opening {image}")
        self.process.setWorkingDirectory(str(ROOT))
        self.process.start(command[0], command[1:])

    def _read_process_output(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            if not line.startswith("WARNING: Unknown module:"):
                self._append_log(line)
            if line.startswith("BCC_EVENT "):
                try:
                    event = json.loads(line[len("BCC_EVENT "):])
                    kind = event.get("kind")
                    if (
                        (self.mode == "validate" and kind == "validation")
                        or (self.mode in {"analyze", "apply-atlas"} and kind == "result")
                        or (self.mode == "cellpose-calibrate" and kind == "done")
                    ):
                        self.current_payload_received = True
                    if kind == "done" and isinstance(event.get("payload"), dict):
                        self.current_done_payload = event["payload"]
                    if kind == "error":
                        self.current_engine_error = str(event.get("payload", "QuPath reported an error."))
                    if kind == "progress" and isinstance(event.get("payload"), dict):
                        payload = event["payload"]
                        entry = float(payload.get("percent", 0)) / 100
                        overall = (self.completed + entry) / self.total * 100
                        self.progress.setValue(round(overall))
                        self.progress_label.setText(f"Image {self.completed + 1}/{self.total} — {payload.get('stage', 'processing')}")
                except (ValueError, TypeError):
                    self._append_log("WARNING: Ignored a malformed BCC_EVENT line.")

    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        image = self.queue.pop(0) if self.queue else "Unknown entry"
        if self.cancelled:
            self._append_log(f"CANCELLED: {image}")
            self._finish_queue()
            return
        if exit_code == 0 and self.current_payload_received and not self.current_engine_error:
            self.completed += 1
            self._append_log(f"COMPLETED: {image}")
            if self.mode == "cellpose-calibrate" and self.current_done_payload is not None:
                payload = self.current_done_payload
                suggested_cellprob = float(payload["suggestedCellprob"])
                suggested_flow = float(payload["suggestedFlow"])
                stability = float(payload["stabilityScore"])
                if QMessageBox.question(
                    self,
                    "Cellpose parameter suggestion",
                    f"Suggested Cellprob: {suggested_cellprob:g}\n"
                    f"Suggested Flow: {suggested_flow:g}\n"
                    f"Local stability score: {stability:.2f}%\n\n"
                    "Apply these heuristic values? Inspect masks visually before a full batch.",
                ) == QMessageBox.StandardButton.Yes:
                    self.cellprob.setText(f"{suggested_cellprob:g}")
                    self.flow.setText(f"{suggested_flow:g}")
                    self.settings["cellprob"] = f"{suggested_cellprob:g}"
                    self.settings["flow"] = f"{suggested_flow:g}"
                    self._save_settings()
                    self._append_log("Suggested Cellprob and Flow values applied.")
            if self.mode == "analyze" and self.checkpoint_signature:
                self.checkpoint_completed.add(image)
                write_checkpoint(
                    checkpoint_path(self.summary.path),
                    self.summary.path,
                    self.checkpoint_signature,
                    self.checkpoint_completed,
                    self.failed,
                )
        else:
            if self.current_engine_error:
                reason = self.current_engine_error
            elif exit_code != 0:
                reason = f"QuPath exited with code {exit_code}"
            else:
                expected = "validation" if self.mode == "validate" else "analysis"
                reason = f"QuPath exited successfully but did not emit an {expected} result"
            self.failed.append(f"{image}: {reason}")
            self._append_log(f"FAILED: {image} — {reason}")
            if self.mode == "analyze" and self.checkpoint_signature:
                write_checkpoint(
                    checkpoint_path(self.summary.path),
                    self.summary.path,
                    self.checkpoint_signature,
                    self.checkpoint_completed,
                    self.failed,
                )
        self.progress.setValue(round((self.completed + len(self.failed)) / self.total * 100))
        self._start_next()

    def _finish_queue(self) -> None:
        self._set_running(False)
        if self.cancelled:
            self.state_label.setText("CANCELLED")
            self.progress_label.setText(f"Cancelled — {self.completed} entries completed")
        elif self.failed:
            self.state_label.setText("ATTENTION")
            self.progress_label.setText(f"Finished — {self.completed} completed, {len(self.failed)} failed")
            self._show_log()
        else:
            self.state_label.setText("COMPLETE")
            self.progress_label.setText(f"Finished — {self.completed} entries completed")
        self._refresh_results()

    def _cancel(self) -> None:
        self.cancelled = True
        self.queue.clear()
        self.threshold_targets.clear()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
        if self.threshold_process.state() != QProcess.ProcessState.NotRunning:
            self.threshold_process.terminate()
        self.progress_label.setText("Cancellation requested")

    def _set_running(self, running: bool) -> None:
        self.state_label.setText("RUNNING" if running else "READY")
        self.validate_button.setEnabled(not running)
        self.analyze_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.threshold_run_button.setEnabled(not running)
        self.results_widget.set_busy(running)

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _show_log(self) -> None:
        self.log_dialog.show()
        self.log_dialog.raise_()
        self.log_dialog.activateWindow()

    def _refresh_results(self) -> None:
        self.results_widget.set_project(self.summary.path if self.summary is not None else None)
        if self.pages.currentIndex() == 3:
            self.results_widget.ensure_loaded()

    def _open_results(self) -> None:
        if self.summary is None:
            return
        folder = self.summary.path.parent / "brain_cell_counter_results"
        folder.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _open_result_in_qupath(self, project_path: Path, image_name: str) -> None:
        """Open the exact saved entry in the interactive QuPath desktop viewer."""
        if self.process.state() != QProcess.ProcessState.NotRunning or self.threshold_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "Open in QuPath", "Wait for the active project operation to finish.")
            return
        executable = qupath_gui_executable(Path(str(self.settings["qupath"])))
        if not executable.is_file():
            QMessageBox.critical(self, "Open in QuPath", "Choose an existing QuPath executable in Settings.")
            return
        subprocess.Popen([str(executable), "-p", str(project_path), "-i", image_name], cwd=str(executable.parent))


def main() -> None:
    """Start the Qt interface using the current monitor's available geometry."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Brain Cell Counter")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)

    def report_runtime_exception(exception_type, exception, trace) -> None:
        """Keep unexpected Qt callback failures visible and diagnosable."""
        details = "".join(traceback.format_exception(exception_type, exception, trace))
        try:
            RUNTIME_ERROR_FILE.write_text(details, encoding="utf-8")
        except OSError:
            pass
        QMessageBox.critical(
            None,
            "Brain Cell Counter interface error",
            f"A screen could not be updated. The application will remain open.\n\n{exception}\n\n"
            f"Diagnostic log: {RUNTIME_ERROR_FILE}",
        )

    sys.excepthook = report_runtime_exception
    window = BrainCellCounterQt()
    activate_main_window(window)
    QTimer.singleShot(250, lambda: activate_main_window(window))
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
