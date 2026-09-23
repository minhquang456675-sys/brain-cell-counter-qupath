"""Native Qt calibration workspace with mandatory human scale-bar review.

The window preserves the established sequential QuPath and Fiji jobs while
matching the visual language of the main application. Original images are
never overwritten, and no proposed calibration is applied before confirmation.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .calibration import (
    CalibrationRecord,
    backup_project_calibration_targets,
    detect_scale_bar,
    is_supported_image,
    match_records_to_project,
    next_fiji_output_path,
    read_calibration_audit,
    read_project_source_entries,
    valid_confirmed_fiji_records,
    valid_confirmed_records,
    write_calibration_audit,
    write_calibration_manifest,
)


CALIBRATION_STYLE = """
QWidget { color: #202123; font-family: 'Segoe UI'; font-size: 10pt; }
QMainWindow, QDialog, #workspace { background: #FFFFFF; }
QFrame[card='true'] { background: #F7F7F8; border: 1px solid #E5E5E5; border-radius: 14px; }
QLabel[heading='true'] { font-size: 21pt; font-weight: 700; }
QLabel[subheading='true'] { font-size: 12pt; font-weight: 600; }
QLabel[metric='true'] { font-size: 18pt; font-weight: 700; }
QLabel[muted='true'] { color: #6B6B6B; }
QLabel[badge='true'] { background: #E8F7F2; color: #08775C; border-radius: 8px; padding: 5px 9px; font-weight: 700; }
QPushButton { border: 1px solid #D9D9DE; border-radius: 9px; padding: 9px 14px; background: #FFFFFF; }
QPushButton:hover { background: #F1F1F2; border-color: #C8C8CE; }
QPushButton:pressed { background: #E7E7E9; padding-top: 10px; padding-bottom: 8px; }
QPushButton:disabled { color: #A5A5AA; background: #F5F5F5; }
QPushButton[primary='true'] { background: #202123; color: white; border-color: #202123; font-weight: 600; }
QPushButton[primary='true']:hover { background: #343541; border-color: #343541; }
QPushButton[primary='true']:disabled { color: #A5A5AA; background: #F5F5F5; border-color: #E5E5E5; }
QPushButton[accent='true'] { background: #10A37F; color: white; border-color: #10A37F; font-weight: 600; }
QPushButton[accent='true']:hover { background: #0D8F70; }
QPushButton[accent='true']:disabled { color: #A5A5AA; background: #F5F5F5; border-color: #E5E5E5; }
QPushButton[danger='true'] { color: #B42318; border-color: #FECDCA; background: #FFF7F6; }
QPushButton[danger='true']:disabled { color: #B7B7B7; background: #F5F5F5; border-color: #E5E5E5; }
QLineEdit, QComboBox, QPlainTextEdit { background: #FFFFFF; border: 1px solid #D9D9DE; border-radius: 8px; padding: 8px; selection-background-color: #10A37F; }
QLineEdit:focus, QComboBox:focus { border-color: #10A37F; }
QTableWidget { background: #FFFFFF; alternate-background-color: #FAFAFA; border: 1px solid #E5E5E5; border-radius: 10px; gridline-color: #EEEEEE; selection-background-color: #DDF3ED; selection-color: #202123; }
QHeaderView::section { background: #F7F7F8; color: #343541; border: 0; border-bottom: 1px solid #E5E5E5; padding: 9px; font-weight: 600; }
QProgressBar { border: 0; border-radius: 4px; background: #ECECEF; height: 8px; color: transparent; }
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
"""


def _button(text: str, *, primary: bool = False, accent: bool = False, danger: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("primary", primary)
    button.setProperty("accent", accent)
    button.setProperty("danger", danger)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _card(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("card", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    heading = QLabel(title)
    heading.setProperty("subheading", True)
    layout.addWidget(heading)
    if description:
        label = QLabel(description)
        label.setProperty("muted", True)
        label.setWordWrap(True)
        layout.addWidget(label)
    return frame, layout


class CalibrationSignals(QObject):
    """Deliver worker-thread events safely to the Qt event loop."""

    event = Signal(str, object)


class ScaleImageLabel(QLabel):
    """Render a review preview and map clicks back to preview coordinates."""

    points_changed = Signal()

    def __init__(self, image: QImage, offset_x: int, offset_y: int, points: list[tuple[float, float]]) -> None:
        super().__init__()
        self.image = image
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.points = points
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setStyleSheet("background:#111111; border-radius:10px;")
        self.setMinimumSize(500, 320)
        self._render()

    def _render(self) -> None:
        available_width = max(500, min(1080, self.width() or 1080))
        available_height = max(320, min(570, self.height() or 570))
        pixmap = QPixmap.fromImage(self.image).scaled(
            available_width,
            available_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter = QPainter(pixmap)
        if len(self.points) == 2:
            scale_x = pixmap.width() / self.image.width()
            scale_y = pixmap.height() / self.image.height()
            mapped = [
                QPointF((x - self.offset_x) * scale_x, (y - self.offset_y) * scale_y)
                for x, y in self.points
            ]
            painter.setPen(QPen(QColor("#FF453A"), 4))
            painter.drawLine(mapped[0], mapped[1])
            painter.setPen(QPen(QColor("#FFE66D"), 3))
            for point in mapped:
                painter.drawEllipse(point, 7, 7)
        painter.end()
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._render()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        pixmap = self.pixmap()
        if pixmap is None:
            return
        x = event.position().x() / max(1, pixmap.width()) * self.image.width() + self.offset_x
        y = event.position().y() / max(1, pixmap.height()) * self.image.height() + self.offset_y
        if len(self.points) >= 2:
            self.points.clear()
        self.points.append((x, y))
        self._render()
        self.points_changed.emit()


class ScaleReviewDialog(QDialog):
    """Collect a human-confirmed physical distance for one scale bar."""

    def __init__(self, record: CalibrationRecord, index: int, total: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.record = record
        self.setWindowTitle(f"Review calibration {index + 1}/{total} — {Path(record.source_path).name}")
        self.setModal(True)
        self.resize(1160, 820)
        self.setMinimumSize(850, 650)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        heading = QLabel("Review scale bar")
        heading.setProperty("heading", True)
        titles.addWidget(heading)
        subtitle = QLabel(f"Image {index + 1} of {total}  •  {Path(record.source_path).name}")
        subtitle.setProperty("muted", True)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        badge = QLabel("CONFIRM MANUALLY")
        badge.setProperty("badge", True)
        header.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        source = QImage(record.preview_path)
        if source.isNull():
            raise ValueError("The calibration preview could not be opened.")
        has_candidate = None not in (record.bar_start_x, record.bar_start_y, record.bar_end_x, record.bar_end_y)
        if has_candidate:
            xs = [int(record.bar_start_x), int(record.bar_end_x)]
            ys = [int(record.bar_start_y), int(record.bar_end_y)]
            left = max(0, min(xs) - 260)
            right = min(source.width(), max(xs) + 261)
            top = max(0, min(ys) - 180)
            bottom = min(source.height(), max(ys) + 181)
            review_image = source.copy(QRect(left, top, right - left, bottom - top))
            offset_x, offset_y = left, top
        else:
            review_image = source
            offset_x = offset_y = 0
        self.points: list[tuple[float, float]] = []
        if has_candidate:
            self.points = [
                (float(record.bar_start_x), float(record.bar_start_y)),
                (float(record.bar_end_x), float(record.bar_end_y)),
            ]
        self.canvas = ScaleImageLabel(review_image, offset_x, offset_y, self.points)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(scroll, 1)

        controls, controls_layout = _card("Physical distance", "Check the red endpoints. Click twice on the image to replace them.")
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("Printed distance"))
        self.distance = QLineEdit("" if record.known_distance is None else f"{record.known_distance:g}")
        self.distance.setPlaceholderText("e.g. 500")
        self.distance.setMaximumWidth(150)
        input_row.addWidget(self.distance)
        self.unit = QComboBox()
        self.unit.addItems(["µm", "mm"])
        self.unit.setCurrentText(record.known_unit or "µm")
        self.unit.setMaximumWidth(90)
        input_row.addWidget(self.unit)
        metadata = "Unavailable" if record.metadata_pixel_width_microns is None else f"{record.metadata_pixel_width_microns:.6g} µm/px"
        self.metadata_label = QLabel(f"Current metadata: {metadata}")
        self.metadata_label.setProperty("muted", True)
        input_row.addWidget(self.metadata_label)
        self.proposal_label = QLabel("Proposal: enter distance")
        self.proposal_label.setProperty("subheading", True)
        input_row.addWidget(self.proposal_label)
        input_row.addStretch()
        controls_layout.addLayout(input_row)
        root.addWidget(controls)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = _button("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        confirm = _button("Confirm & next", accent=True)
        confirm.clicked.connect(self._confirm)
        buttons.addWidget(confirm)
        root.addLayout(buttons)
        self.distance.textChanged.connect(self._calculate)
        self.unit.currentTextChanged.connect(self._calculate)
        self.canvas.points_changed.connect(self._calculate)
        self._calculate()

    def _calculate(self) -> float | None:
        if len(self.points) != 2:
            self.proposal_label.setText("Proposal: select two endpoints")
            return None
        try:
            distance = float(self.distance.text())
        except ValueError:
            self.proposal_label.setText("Proposal: enter a valid distance")
            return None
        if distance <= 0:
            self.proposal_label.setText("Proposal: distance must be > 0")
            return None
        preview_length = math.dist(self.points[0], self.points[1])
        microns = distance * (1000.0 if self.unit.currentText() == "mm" else 1.0)
        proposal = microns / (preview_length * (self.record.downsample or 1.0))
        self.proposal_label.setText(f"Proposal: {proposal:.6g} µm/px")
        return proposal

    def _confirm(self) -> None:
        proposal = self._calculate()
        if proposal is None:
            QMessageBox.warning(self, "Calibration review", "Select both endpoints and enter the printed distance.")
            return
        if not 0.01 <= proposal <= 100.0 and QMessageBox.question(
            self,
            "Unusual calibration",
            f"The proposed value is {proposal:.6g} µm/px, outside the usual 0.01–100 range. Confirm anyway?",
        ) != QMessageBox.StandardButton.Yes:
            return
        metadata = self.record.metadata_pixel_width_microns
        if metadata and metadata > 0:
            difference = abs(proposal - metadata) / metadata * 100.0
            if difference > 5.0 and QMessageBox.question(
                self,
                "Metadata conflict",
                f"Scale-bar calibration differs from metadata by {difference:.1f}%.\n\nMetadata: {metadata:.6g} µm/px\nScale bar: {proposal:.6g} µm/px\n\nConfirm the scale-bar result?",
            ) != QMessageBox.StandardButton.Yes:
                return
        self.record.bar_start_x = round(self.points[0][0])
        self.record.bar_start_y = round(self.points[0][1])
        self.record.bar_end_x = round(self.points[1][0])
        self.record.bar_end_y = round(self.points[1][1])
        self.record.bar_preview_length_pixels = math.dist(self.points[0], self.points[1])
        self.record.known_distance = float(self.distance.text())
        self.record.known_unit = self.unit.currentText()
        self.record.proposed_pixel_size_microns = proposal
        self.record.confirmed = True
        self.record.applied = False
        self.record.note = "Human-confirmed scale-bar calibration."
        self.accept()


class QtCalibrationWindow(QMainWindow):
    """Run the complete calibration workflow in a native ChatGPT-style UI."""

    def __init__(self, qupath_path: Path, fiji_path: Path, project_path: Path) -> None:
        super().__init__()
        self.qupath_path = qupath_path
        self.fiji_path = fiji_path
        self.project_path = project_path
        self.records: list[CalibrationRecord] = []
        self.process: subprocess.Popen | None = None
        self.running = False
        self.stop_requested = False
        root = Path(__file__).resolve().parent.parent
        self.preview_script = root / "qupath_scripts" / "Brain_Cell_Counter_Calibration_Preview_v1.0.groovy"
        self.apply_script = root / "qupath_scripts" / "Brain_Cell_Counter_Calibration_Apply_v1.0.groovy"
        self.fiji_script = root / "qupath_scripts" / "Brain_Cell_Counter_Fiji_Calibration_v1.0.groovy"
        self.output_directory = self.project_path.parent / "brain_cell_counter_calibration"
        self.qc_directory = self.output_directory / "qc"
        self.fiji_output_directory = self.output_directory / "fiji_calibrated"
        self.fiji_report_directory = self.output_directory / "fiji_verification"
        self.signals = CalibrationSignals()
        self.signals.event.connect(self._handle_event)
        self.setWindowTitle("Brain Cell Counter — Calibration")
        self.setStyleSheet(CALIBRATION_STYLE)
        self.resize(1280, 860)
        self.setMinimumSize(980, 680)
        self._build_ui()
        self._restore_session()

    def _restore_session(self) -> None:
        """Load the prior audit and ensure every current project image is listed."""
        try:
            self.records = read_calibration_audit(self._audit_path())
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.records = []
            self._append_log(f"Existing calibration audit could not be restored: {exc}")
        restored = len(self.records)
        self._add_project_images()
        if restored:
            self._append_log(f"Restored {restored} record(s) from the previous calibration audit.")

    def _build_ui(self) -> None:
        workspace = QWidget()
        workspace.setObjectName("workspace")
        self.setCentralWidget(workspace)
        root = QVBoxLayout(workspace)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(12)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        heading = QLabel("Calibration")
        heading.setProperty("heading", True)
        titles.addWidget(heading)
        subtitle = QLabel("Human-reviewed scale recovery for stitched microscopy images")
        subtitle.setProperty("muted", True)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        badge = QLabel("HUMAN REVIEW REQUIRED")
        badge.setProperty("badge", True)
        header.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        steps = QHBoxLayout()
        for title, description in (
            ("1  Add images", "Choose files, a folder, or every project entry."),
            ("2  Scan & review", "Verify endpoints and the printed distance."),
            ("3  Export or apply", "Create verified copies or backed-up metadata."),
        ):
            card, layout = _card(title, description)
            card.setFixedHeight(82)
            steps.addWidget(card, 1)
        root.addLayout(steps)

        metrics = QHBoxLayout()
        self.metric_labels: dict[str, QLabel] = {}
        for key, title in (("images", "Images"), ("scanned", "Scanned"), ("confirmed", "Confirmed"), ("matched", "Project matched")):
            card = QFrame()
            card.setProperty("card", True)
            card.setFixedHeight(78)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(16, 10, 16, 10)
            layout.setSpacing(1)
            value = QLabel("0")
            value.setProperty("metric", True)
            layout.addWidget(value)
            label = QLabel(title)
            label.setProperty("muted", True)
            layout.addWidget(label)
            self.metric_labels[key] = value
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

        batch_card, batch = _card("Calibration batch", "Double-click a scanned row to review its scale bar.")
        toolbar = QHBoxLayout()
        self.add_files_button = _button("Add images…")
        self.add_files_button.clicked.connect(self._add_files)
        toolbar.addWidget(self.add_files_button)
        self.add_folder_button = _button("Add folder…")
        self.add_folder_button.clicked.connect(self._add_folder)
        toolbar.addWidget(self.add_folder_button)
        self.add_project_button = _button("Add project images")
        self.add_project_button.clicked.connect(self._add_project_images)
        toolbar.addWidget(self.add_project_button)
        self.remove_button = _button("Remove", danger=True)
        self.remove_button.clicked.connect(self._remove_selected)
        toolbar.addWidget(self.remove_button)
        toolbar.addStretch()
        self.scan_button = _button("Scan scale bars", primary=True)
        self.scan_button.clicked.connect(self._start_scan)
        toolbar.addWidget(self.scan_button)
        batch.addLayout(toolbar)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["File", "QuPath entry", "Metadata µm/px", "Bar px", "Known distance", "Proposed µm/px", "Detection", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(190)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 8):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.cellDoubleClicked.connect(lambda row, _column: self._open_review(row))
        batch.addWidget(self.table, 1)
        root.addWidget(batch_card, 1)

        actions = QHBoxLayout()
        self.review_button = _button("Review selected", primary=True)
        self.review_button.clicked.connect(self._review_selected)
        actions.addWidget(self.review_button)
        self.export_button = _button("Export manifest")
        self.export_button.clicked.connect(self._export_manifest)
        actions.addWidget(self.export_button)
        self.apply_button = _button("Apply confirmed to QuPath")
        self.apply_button.clicked.connect(self._start_apply)
        actions.addWidget(self.apply_button)
        self.fiji_button = _button("Export calibrated with Fiji", accent=True)
        self.fiji_button.clicked.connect(self._start_fiji_export)
        actions.addWidget(self.fiji_button)
        self.stop_button = _button("Stop", danger=True)
        self.stop_button.clicked.connect(self._stop)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        close = _button("Close")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        root.addLayout(actions)

        activity, activity_layout = _card("Activity")
        self.progress_label = QLabel("Ready")
        self.progress_label.setProperty("muted", True)
        activity_layout.addWidget(self.progress_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        activity_layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(70)
        self.log.setPlaceholderText("Calibration events will appear here.")
        activity_layout.addWidget(self.log)
        root.addWidget(activity)
        self._update_actions()

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def _set_running(self, running: bool) -> None:
        self.running = running
        self._update_actions()

    def _update_actions(self) -> None:
        selected = self._selected_rows() if hasattr(self, "table") else []
        selected_scanned = bool(selected and self.records[selected[0]].preview_path)
        for button in (self.add_files_button, self.add_folder_button, self.add_project_button):
            button.setEnabled(not self.running)
        self.remove_button.setEnabled(not self.running and bool(selected))
        self.scan_button.setEnabled(not self.running and bool(self.records))
        self.review_button.setEnabled(not self.running and (selected_scanned or any(record.preview_path for record in self.records)))
        self.export_button.setEnabled(not self.running and bool(self.records))
        self.apply_button.setEnabled(not self.running and bool(valid_confirmed_records(self.records)))
        self.fiji_button.setEnabled(not self.running and bool(valid_confirmed_fiji_records(self.records)))
        self.stop_button.setEnabled(self.running)

    def _add_paths(self, paths: list[Path]) -> None:
        existing = {str(Path(record.source_path).resolve()).casefold() for record in self.records}
        additions: list[CalibrationRecord] = []
        for path in paths:
            if not is_supported_image(path):
                continue
            resolved = str(path.resolve()).casefold()
            if resolved in existing:
                continue
            additions.append(CalibrationRecord(source_path=str(path.resolve())))
            existing.add(resolved)
        match_records_to_project(additions, self.project_path)
        self.records.extend(additions)
        self._refresh_table()
        self._append_log(f"Added {len(additions)} image(s). Total: {len(self.records)}.")

    def _add_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Add stitched images", "", "Microscopy images (*.tif *.tiff *.png *.jpg *.jpeg);;All files (*)")
        self._add_paths([Path(path) for path in paths])

    def _add_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Add images from folder")
        if not selected:
            return
        paths = [path for path in Path(selected).rglob("*") if is_supported_image(path)]
        if len(paths) > 100 and QMessageBox.question(self, "Large calibration batch", f"The folder contains {len(paths)} supported images. Add all of them?") != QMessageBox.StandardButton.Yes:
            return
        self._add_paths(paths)

    def _add_project_images(self) -> None:
        entries = read_project_source_entries(self.project_path)
        self._add_paths([entry["source_path"] for entry in entries if isinstance(entry.get("source_path"), Path)])

    def _remove_selected(self) -> None:
        for row in reversed(self._selected_rows()):
            self.records.pop(row)
        self._refresh_table()

    def _refresh_table(self) -> None:
        selected_paths = {self.records[row].source_path for row in self._selected_rows() if row < len(self.records)}
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            metadata = "" if record.metadata_pixel_width_microns is None else f"{record.metadata_pixel_width_microns:.6g}"
            bar = "" if not record.bar_preview_length_pixels else f"{record.bar_preview_length_pixels:.1f}"
            distance = "" if record.known_distance is None else f"{record.known_distance:g} {record.known_unit}"
            proposal = "" if record.proposed_pixel_size_microns is None else f"{record.proposed_pixel_size_microns:.6g}"
            confidence = "" if record.detection_confidence is None else f"{record.detection_confidence * 100:.0f}%"
            if record.fiji_verified:
                status, color = "Fiji verified", QColor("#ECFDF3")
            elif record.applied:
                status, color = "Applied", QColor("#ECFDF3")
            elif record.confirmed:
                status, color = "Confirmed", QColor("#ECFDF3")
            elif record.preview_path:
                status, color = "Review required", QColor("#FFFAEB")
            elif record.note:
                status, color = "Needs attention", QColor("#FEF3F2")
            else:
                status, color = "Not scanned", QColor("#FFFFFF")
            values = [Path(record.source_path).name, record.image_name or "Not matched", metadata, bar, distance, proposal, confidence, status]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(color)
                self.table.setItem(row, column, item)
            if record.source_path in selected_paths:
                self.table.selectRow(row)
        self.metric_labels["images"].setText(str(len(self.records)))
        self.metric_labels["scanned"].setText(str(sum(bool(record.preview_path) for record in self.records)))
        self.metric_labels["confirmed"].setText(str(sum(bool(record.confirmed) for record in self.records)))
        self.metric_labels["matched"].setText(str(sum(bool(record.image_name) for record in self.records)))
        self._update_actions()

    @staticmethod
    def _decode_event(line: str) -> dict | None:
        if not line.startswith("BCC_EVENT "):
            return None
        try:
            event = json.loads(line[len("BCC_EVENT "):])
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return event if isinstance(event, dict) else None

    def _preview_command(self, record: CalibrationRecord, output: Path) -> list[str]:
        command = [str(self.qupath_path), "-q", "script"]
        if record.image_name:
            command.extend(["-p", str(self.project_path), "-i", record.image_name])
        else:
            command.extend(["-i", record.source_path])
        command.extend([str(self.preview_script), "--args", f"outputPath={output.as_posix()}", "--args", "maxDimension=4000"])
        return command

    def _start_scan(self) -> None:
        if not self.records:
            return
        if not self.qupath_path.is_file() or not self.preview_script.is_file():
            QMessageBox.critical(self, "Calibration", "QuPath or the calibration preview script is missing.")
            return
        self.qc_directory.mkdir(parents=True, exist_ok=True)
        self.stop_requested = False
        self.progress.setValue(0)
        self.progress_label.setText("Scanning images sequentially")
        self._set_running(True)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        total = len(self.records)
        for index, record in enumerate(self.records):
            if self.stop_requested:
                break
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(record.source_path).stem)[:80]
            output = self.qc_directory / f"{index + 1:03d}_{safe_name}_preview.png"
            self.signals.event.emit("log", f"[{index + 1}/{total}] Scanning {Path(record.source_path).name}")
            payload = None
            tail: list[str] = []
            try:
                self.process = subprocess.Popen(self._preview_command(record, output), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
                assert self.process.stdout is not None
                for raw in self.process.stdout:
                    line = raw.rstrip()
                    if line:
                        tail = (tail + [line])[-12:]
                    event = self._decode_event(line)
                    if event and event.get("kind") == "calibration_preview":
                        payload = event.get("payload")
                return_code = self.process.wait()
                if return_code != 0 or not isinstance(payload, dict):
                    raise RuntimeError("\n".join(tail[-6:]) or f"QuPath exited with code {return_code}.")
                record.source_width = int(payload["sourceWidth"])
                record.source_height = int(payload["sourceHeight"])
                record.preview_width = int(payload["previewWidth"])
                record.preview_height = int(payload["previewHeight"])
                record.downsample = float(payload["downsample"])
                record.preview_path = str(output)
                record.metadata_pixel_width_microns = payload.get("metadataPixelWidth")
                record.metadata_pixel_height_microns = payload.get("metadataPixelHeight")
                candidate = detect_scale_bar(output)
                if candidate:
                    record.bar_start_x, record.bar_start_y = candidate.start_x, candidate.start_y
                    record.bar_end_x, record.bar_end_y = candidate.end_x, candidate.end_y
                    record.bar_preview_length_pixels = candidate.preview_length_pixels
                    record.detection_confidence = candidate.confidence
                    record.note = "Automatic endpoint proposal; human review required."
                else:
                    record.note = "No scale bar candidate detected; select endpoints manually."
                self.signals.event.emit("refresh", index)
            except Exception as exc:
                record.note = f"Scan failed: {exc}"
                self.signals.event.emit("log", f"[{index + 1}/{total}] FAILED: {exc}")
                self.signals.event.emit("refresh", index)
            finally:
                self.process = None
            self.signals.event.emit("progress", ((index + 1) / total * 100.0, f"Scanned {index + 1}/{total}"))
        self.signals.event.emit("scan_done", None)

    def _review_selected(self) -> None:
        rows = self._selected_rows()
        index = rows[0] if rows else next((i for i, record in enumerate(self.records) if record.preview_path and not record.confirmed), -1)
        if index < 0:
            QMessageBox.information(self, "Calibration review", "Select a scanned image to review.")
            return
        self._open_review(index)

    def _open_review(self, index: int) -> None:
        if index < 0 or index >= len(self.records):
            return
        record = self.records[index]
        if not Path(record.preview_path).is_file():
            QMessageBox.warning(self, "Calibration review", "Scan this image before reviewing it.")
            return
        try:
            dialog = ScaleReviewDialog(record, index, len(self.records), self)
        except ValueError as exc:
            QMessageBox.critical(self, "Calibration review", str(exc))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._refresh_table()
        next_index = next((candidate for candidate in range(index + 1, len(self.records)) if self.records[candidate].preview_path and not self.records[candidate].confirmed), None)
        if next_index is not None:
            self.table.selectRow(next_index)
            self._open_review(next_index)

    def _manifest_path(self) -> Path:
        return self.output_directory / "calibration_manifest_v1.2.csv"

    def _audit_path(self) -> Path:
        return self.output_directory / "calibration_audit_v1.2.json"

    def _write_outputs(self) -> Path:
        path = write_calibration_manifest(self.records, self._manifest_path())
        write_calibration_audit(self.records, self._audit_path())
        return path

    def _export_manifest(self) -> None:
        if not self.records:
            return
        path = self._write_outputs()
        self._append_log(f"Compact manifest written: {path}")
        QMessageBox.information(self, "Calibration manifest", f"Compact CSV:\n{path}\n\nFull JSON audit:\n{self._audit_path()}")

    def _fiji_command(self, record: CalibrationRecord, output: Path, report: Path) -> list[str]:
        if any("'" in value for value in (record.source_path, str(output), str(report))):
            raise ValueError("Fiji calibration paths cannot contain an apostrophe.")
        parameters = f"sourceFile='{Path(record.source_path).as_posix()}',outputFile='{output.as_posix()}',reportFile='{report.as_posix()}',pixelSizeMicrons='{record.proposed_pixel_size_microns:.12g}'"
        arguments = ["--headless", "--console", "--run", str(self.fiji_script), parameters]
        batch = self.fiji_path.parent / "fiji.bat"
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(batch), *arguments] if batch.is_file() else [str(self.fiji_path), *arguments]

    def _start_fiji_export(self) -> None:
        confirmed = valid_confirmed_fiji_records(self.records)
        if not confirmed:
            return
        if not self.fiji_path.is_file() or not self.fiji_script.is_file():
            QMessageBox.critical(self, "Fiji calibration export", "Choose an existing Fiji executable in Settings.")
            return
        lines = "\n".join(f"{Path(record.source_path).name}: {record.proposed_pixel_size_microns:.6g} µm/px" for record in confirmed[:20])
        if QMessageBox.question(self, "Confirm Fiji calibrated copies", f"Export these human-confirmed calibrations as new OME-TIFF files?\n\n{lines}\n\nOriginal images will not be overwritten.") != QMessageBox.StandardButton.Yes:
            return
        self.fiji_output_directory.mkdir(parents=True, exist_ok=True)
        self.fiji_report_directory.mkdir(parents=True, exist_ok=True)
        self._write_outputs()
        self.stop_requested = False
        self.progress.setValue(0)
        self.progress_label.setText("Exporting calibrated OME-TIFF files through Fiji")
        self._set_running(True)
        threading.Thread(target=self._fiji_worker, args=(confirmed,), daemon=True).start()

    def _fiji_worker(self, records: list[CalibrationRecord]) -> None:
        failures: list[str] = []
        for index, record in enumerate(records):
            if self.stop_requested:
                break
            output = next_fiji_output_path(self.fiji_output_directory, Path(record.source_path))
            report = self.fiji_report_directory / f"{output.stem}_verification.json"
            self.signals.event.emit("log", f"[{index + 1}/{len(records)}] Fiji export: {Path(record.source_path).name}")
            try:
                self.process = subprocess.Popen(self._fiji_command(record, output, report), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
                assert self.process.stdout is not None
                tail = [line.rstrip() for line in self.process.stdout if line.strip()][-12:]
                return_code = self.process.wait()
                if return_code != 0 or not output.is_file() or not report.is_file():
                    raise RuntimeError("\n".join(tail) or f"Fiji exited with code {return_code}.")
                result = json.loads(report.read_text(encoding="utf-8"))
                if not bool(result.get("verified")):
                    raise RuntimeError("Fiji verification did not pass.")
                expected = float(record.proposed_pixel_size_microns)
                width, height = float(result["pixelWidthMicrons"]), float(result["pixelHeightMicrons"])
                tolerance = max(1.0e-9, expected * 1.0e-6)
                if abs(width - expected) > tolerance or abs(height - expected) > tolerance:
                    raise RuntimeError("The verification report contains a different pixel size.")
                record.fiji_output_path = str(output)
                record.fiji_verified_pixel_width_microns = width
                record.fiji_verified_pixel_height_microns = height
                record.fiji_verified = True
                record.note = "Human-confirmed calibration exported and verified by Fiji."
                self.signals.event.emit("log", f"[{index + 1}/{len(records)}] VERIFIED: {output.name}")
            except Exception as exc:
                failures.append(f"{Path(record.source_path).name}: {exc}")
                record.fiji_verified = False
                record.note = f"Fiji export failed: {exc}"
                self.signals.event.emit("log", f"[{index + 1}/{len(records)}] FAILED: {exc}")
            finally:
                self.process = None
            self.signals.event.emit("refresh", index)
            self.signals.event.emit("progress", ((index + 1) / len(records) * 100.0, f"Fiji export {index + 1}/{len(records)}"))
        self._write_outputs()
        self.signals.event.emit("fiji_done", failures)

    def _start_apply(self) -> None:
        confirmed = valid_confirmed_records(self.records)
        if not confirmed:
            return
        lines = "\n".join(f"{record.image_name}: {record.proposed_pixel_size_microns:.6g} µm/px" for record in confirmed[:20])
        if QMessageBox.question(self, "Confirm QuPath calibration write", f"Apply these human-confirmed calibrations to QuPath metadata?\n\n{lines}\n\nSource images will not change. Backups are created first.") != QMessageBox.StandardButton.Yes:
            return
        try:
            backups = backup_project_calibration_targets(self.project_path, confirmed)
            self._write_outputs()
        except OSError as exc:
            QMessageBox.critical(self, "Apply calibration", f"Could not create backups:\n{exc}")
            return
        self._append_log(f"Created {len(backups)} backup file(s) before calibration write.")
        self.stop_requested = False
        self.progress.setValue(0)
        self.progress_label.setText("Applying confirmed calibration sequentially")
        self._set_running(True)
        threading.Thread(target=self._apply_worker, args=(confirmed,), daemon=True).start()

    def _apply_worker(self, records: list[CalibrationRecord]) -> None:
        failures: list[str] = []
        for index, record in enumerate(records):
            if self.stop_requested:
                break
            command = [str(self.qupath_path), "-q", "script", "-p", str(self.project_path), "-i", record.image_name, "--save", str(self.apply_script), "--args", f"pixelSizeMicrons={record.proposed_pixel_size_microns:.12g}"]
            applied = False
            tail: list[str] = []
            try:
                self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
                assert self.process.stdout is not None
                for raw in self.process.stdout:
                    line = raw.rstrip()
                    if line:
                        tail = (tail + [line])[-10:]
                    event = self._decode_event(line)
                    if event and event.get("kind") == "calibration_applied":
                        applied = True
                return_code = self.process.wait()
                if return_code != 0 or not applied:
                    raise RuntimeError("\n".join(tail[-5:]) or f"QuPath exited with code {return_code}.")
                record.applied = True
                self.signals.event.emit("log", f"[{index + 1}/{len(records)}] Applied: {record.image_name}")
            except Exception as exc:
                failures.append(f"{record.image_name}: {exc}")
                self.signals.event.emit("log", f"[{index + 1}/{len(records)}] FAILED: {record.image_name}: {exc}")
            finally:
                self.process = None
            self.signals.event.emit("refresh", index)
            self.signals.event.emit("progress", ((index + 1) / len(records) * 100.0, f"Applied {index + 1}/{len(records)}"))
        self._write_outputs()
        self.signals.event.emit("apply_done", failures)

    def _stop(self) -> None:
        self.stop_requested = True
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self._append_log("Stop requested. No additional image will start.")

    def _handle_event(self, kind: str, payload: object) -> None:
        if kind == "log":
            self._append_log(str(payload))
        elif kind == "refresh":
            self._refresh_table()
        elif kind == "progress":
            percent, text = payload  # type: ignore[misc]
            self.progress.setValue(round(float(percent)))
            self.progress_label.setText(str(text))
        elif kind == "scan_done":
            self._set_running(False)
            scanned = sum(bool(record.preview_path) for record in self.records)
            self.progress_label.setText(f"Scan complete — {scanned}/{len(self.records)} previews")
            first = next((i for i, record in enumerate(self.records) if record.preview_path and not record.confirmed), None)
            if first is not None:
                self.table.selectRow(first)
                self._open_review(first)
        elif kind in {"apply_done", "fiji_done"}:
            self._set_running(False)
            failures = list(payload or [])  # type: ignore[arg-type]
            if failures:
                self.progress_label.setText("Completed with errors — review the activity log")
                QMessageBox.warning(self, "Calibration", f"Completed with {len(failures)} failure(s). See the activity log and manifest.")
            else:
                self.progress.setValue(100)
                self.progress_label.setText("Calibration operation completed")
                QMessageBox.information(self, "Calibration", "The calibration operation completed successfully.")

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self.running:
            QMessageBox.warning(self, "Calibration", "Stop the active calibration task before closing this window.")
            event.ignore()
            return
        event.accept()


def run_calibration_window(qupath_path: Path, fiji_path: Path, project_path: Path) -> int:
    """Create and run the standalone native Qt calibration window."""
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = QtCalibrationWindow(qupath_path, fiji_path, project_path)
    available = window.screen().availableGeometry()
    width = min(1280, max(window.minimumWidth(), available.width() - 48))
    height = min(820, max(window.minimumHeight(), available.height() - 48))
    window.resize(width, height)
    window.move(available.center() - window.rect().center())
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()
