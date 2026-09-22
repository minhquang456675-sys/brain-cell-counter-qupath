"""Modern Qt front end for Brain Cell Counter.

The interface keeps the scientific engine unchanged.  It sends one QuPath
entry at a time through the shared command builder so project writes and GPU
work remain strictly sequential.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import ctypes
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QProcess, QPropertyAnimation, QTimer, Qt, QUrl
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
    QHBoxLayout,
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
    ENGINE_FILENAME,
    analysis_arguments,
    analysis_signature,
    checkpoint_path,
    qupath_command,
    qupath_script_path,
    read_checkpoint,
    read_project,
    write_checkpoint,
)
from .version import APP_VERSION_LABEL


ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = ROOT / "brain_cell_counter_settings.json"
DEFAULT_QUPATH = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "QuPath-0.7.0" / "QuPath-0.7.0 (console).exe"
DEFAULT_CELLPOSE = Path.home() / "miniforge3" / "envs" / "cellpose" / "python.exe"


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
        self.resize(720, 280)
        self.settings = settings
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.qupath = QLineEdit(str(settings["qupath"]))
        self.cellpose = QLineEdit(str(settings["cellpose_python"]))
        form.addRow("QuPath Console", self._path_row(self.qupath, "Choose QuPath", "Executable (*.exe)"))
        form.addRow("Cellpose Python", self._path_row(self.cellpose, "Choose Cellpose Python", "python.exe"))
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
        self.cancelled = False
        self.mode = "validate"
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.finished.connect(self._process_finished)
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
            "reuse_detections": True,
            "hemisphere_mode": "Left and Right",
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
            settings["hemisphere_mode"] = saved.get("hemisphere_mode", settings["hemisphere_mode"])
            settings["qupath"] = saved.get("qupath_path", settings["qupath"])
            settings["cellpose_python"] = saved.get("cellpose_python_path", settings["cellpose_python"])
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
        previous_thresholds = data.get("global_thresholds", {})
        threshold_locked = bool(previous_thresholds.get("locked", False)) if isinstance(previous_thresholds, dict) else False
        data.update({
            "project": str(self.settings["project"]),
            "qupath_path": str(self.settings["qupath"]),
            "cellpose_python_path": str(self.settings["cellpose_python"]),
            "channel_names": {str(i): str(self.settings[f"channel_{i}_name"]) for i in range(1, 4)},
            "hemisphere_mode": str(self.settings["hemisphere_mode"]),
            "global_thresholds": {
                **{f"channel{i}": str(self.settings[f"channel_{i}_threshold"]) for i in range(1, 4)},
                "locked": threshold_locked,
            },
            "cellpose_parameters": {
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
            ("1. Calibration", "Human-confirm scale bars before writing any calibration.", "Open calibration", self._open_classic),
            ("2. Atlas", "Import each entry's own exported ABBA registration.", "Import ABBA", self._start_abba),
            ("3. File links", "Report missing source images before processing.", "Run preflight", self._preflight),
        ):
            frame, box = _card(title, text)
            button = _button(action)
            button.clicked.connect(callback)
            box.addWidget(button)
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
        behavior_form.addRow("Detections", self.reuse)
        behavior_form.addRow("Hemisphere", self.hemisphere)
        behavior_form.addRow("Cellprob threshold", self.cellprob)
        behavior_form.addRow("Flow threshold", self.flow)
        behavior.addLayout(behavior_form)
        layout.addWidget(behavior_card)
        layout.addStretch()
        return page

    def _threshold_page(self) -> QWidget:
        page, layout = self._scroll_page("Thresholds", "Find one stable dataset-wide threshold per channel or channel pair.")
        card, box = _card("Global plateau finder", "The current optimizer and its complete audit report remain available during the Qt migration.")
        note = QLabel("This advanced workflow opens in the proven classic controls so the threshold algorithm and saved profiles remain unchanged.")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        box.addWidget(note)
        open_button = _button("Open threshold finder", primary=True)
        open_button.clicked.connect(self._open_classic)
        box.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _results_page(self) -> QWidget:
        page, layout = self._scroll_page("Results", "Open versioned CSV and atlas-overlay outputs without modifying master files.")
        card, box = _card("Project results")
        self.results_path = QLabel("No result folder selected")
        self.results_path.setProperty("muted", True)
        self.results_path.setWordWrap(True)
        box.addWidget(self.results_path)
        row = QHBoxLayout()
        refresh = _button("Refresh")
        refresh.clicked.connect(self._refresh_results)
        row.addWidget(refresh)
        open_folder = _button("Open results folder", primary=True)
        open_folder.clicked.connect(self._open_results)
        row.addWidget(open_folder)
        full_tools = _button("Open full results tools")
        full_tools.clicked.connect(self._open_classic)
        row.addWidget(full_tools)
        row.addStretch()
        box.addLayout(row)
        self.result_list = QListWidget()
        box.addWidget(self.result_list)
        layout.addWidget(card)
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
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setProperty("selected", button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)
        if animate:
            effect = self.pages.currentWidget().graphicsEffect()
            if effect is None:
                from PySide6.QtWidgets import QGraphicsOpacityEffect
                effect = QGraphicsOpacityEffect(self.pages.currentWidget())
                self.pages.currentWidget().setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", self)
            animation.setDuration(180)
            animation.setStartValue(0.35)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._page_animation = animation
            animation.start()

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
        self._save_settings()
        self._refresh_results()

    def _collect_settings(self) -> None:
        for index, field in enumerate(self.name_fields, 1):
            self.settings[f"channel_{index}_name"] = field.text().strip()
        for index, field in enumerate(self.threshold_fields, 1):
            self.settings[f"channel_{index}_threshold"] = field.text().strip()
        self.settings["require_channel_3"] = self.require_channel_3.isChecked()
        self.settings["reuse_detections"] = self.reuse.isChecked()
        self.settings["hemisphere_mode"] = self.hemisphere.currentText()
        self.settings["cellprob"] = self.cellprob.text().strip()
        self.settings["flow"] = self.flow.text().strip()
        self._save_settings()

    def _show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings["qupath"] = dialog.qupath.text().strip()
            self.settings["cellpose_python"] = dialog.cellpose.text().strip()
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
6. Review Channel 1/2/3 names, thresholds, Cellprob, Flow, reuse, and hemisphere mode.
7. Click **Analyze**, choose entries, and confirm. Entries run sequentially from top to bottom.
8. Review regional CSV files, atlas overlays, the run log, and saved detections in QuPath.

## Segmentation and classification

Cellpose-SAM creates DAPI-defined objects. Channel 1 and Channel 2 positivity is then classified from the mean intensity inside each object. Counts are object-based co-expression, not pixel-level colocalization. Lower Cellprob generally creates more masks; a lower Flow threshold rejects more questionable masks.

## Global threshold algorithm

For every candidate threshold, the optimizer counts objects in every section. A step is stable only when total count change is below the configured total limit and the required percentage of individual sections is also stable. A plateau requires several consecutive stable steps. The proposal is the highest threshold inside the stable plateau before a clear loss of detections. Channels are optimized independently unless a positive-pair objective is selected.

The automatic background guard estimates a negative/positive split from the dataset-wide intensity distribution and rejects saturated low-threshold plateaus. All proposals still require controls, intensity distributions, and visual inspection. Never choose a threshold only because it produces the most cells.

## Safety and reproducibility

- Source TIFF files are never overwritten.
- Only one QuPath/Cellpose process runs at a time.
- Analysis checkpoints are accepted only when the project and exact parameters match.
- Physical area and density remain blank when calibration is unknown.
- Close the QuPath desktop before an operation that writes the project.

Calibration, the complete threshold audit, compact result-column export, and **Open in QuPath** remain available through the classic compatibility tools during the Qt migration.
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
        else:
            choice = QMessageBox.question(
                self,
                "Preflight",
                "Missing source images:\n\n- " + "\n- ".join(missing)
                + "\n\nOpen the classic repair workflow?",
            )
            if choice == QMessageBox.StandardButton.Yes:
                self._open_classic()

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

    def _start_validation(self) -> None:
        if self.summary is not None:
            self._start_queue("validate", [entry.name for entry in self.summary.entries])
        else:
            QMessageBox.warning(self, "Validate", "Choose a QuPath project first.")

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
        if self.process.state() != QProcess.ProcessState.NotRunning:
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
                    if (self.mode == "validate" and kind == "validation") or (self.mode != "validate" and kind == "result"):
                        self.current_payload_received = True
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
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
        self.progress_label.setText("Cancellation requested")

    def _set_running(self, running: bool) -> None:
        self.state_label.setText("RUNNING" if running else "READY")
        self.validate_button.setEnabled(not running)
        self.analyze_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _show_log(self) -> None:
        self.log_dialog.show()
        self.log_dialog.raise_()
        self.log_dialog.activateWindow()

    def _refresh_results(self) -> None:
        self.result_list.clear()
        if self.summary is None:
            return
        folder = self.summary.path.parent / "brain_cell_counter_results"
        self.results_path.setText(str(folder))
        if folder.is_dir():
            for path in sorted(folder.glob("*.csv"), key=lambda item: item.stat().st_mtime, reverse=True):
                self.result_list.addItem(path.name)

    def _open_results(self) -> None:
        if self.summary is None:
            return
        folder = self.summary.path.parent / "brain_cell_counter_results"
        folder.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _open_classic(self) -> None:
        self._collect_settings()
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = pythonw if pythonw.is_file() else Path(sys.executable)
        subprocess.Popen([str(executable), str(ROOT / "run_brain_cell_counter_v1.0.py"), "--classic"], cwd=str(ROOT))


def main() -> None:
    """Start the Qt interface using the current monitor's available geometry."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Brain Cell Counter")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = BrainCellCounterQt()
    activate_main_window(window)
    QTimer.singleShot(250, lambda: activate_main_window(window))
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
