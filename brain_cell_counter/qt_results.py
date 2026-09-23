"""Native Qt preview for versioned regional result tables.

This module reuses the toolkit-independent CSV helpers.  Display and export
choices never alter the complete scientific result file.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .results_preview import (
    COUNT_HINTS,
    ESSENTIAL_IDENTIFIER_COLUMNS,
    IDENTIFIER_COLUMNS,
    PERCENT_HINT,
    PHYSICAL_HINTS,
    atlas_overlay_for_result,
    discover_regional_csvs,
    export_selected_columns,
    read_preview_csv,
    result_image_entry,
)


class QtResultsWidget(QWidget):
    """Preview, filter, export, and inspect regional results in QuPath."""

    def __init__(
        self,
        open_qupath: Callable[[Path, str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.open_qupath = open_qupath
        self.project_path: Path | None = None
        self.paths: dict[str, Path] = {}
        self.headers: list[str] = []
        self.rows: list[dict[str, str]] = []
        self.visible: dict[str, bool] = {}
        self.busy = False
        self.loaded_path: Path | None = None

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Regional CSV"))
        self.file_combo = QComboBox()
        self.file_combo.currentTextChanged.connect(self._load_selected)
        toolbar.addWidget(self.file_combo, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        self.open_qupath_button = QPushButton("Open in QuPath")
        self.open_qupath_button.clicked.connect(self._open_in_qupath)
        toolbar.addWidget(self.open_qupath_button)
        folder = QPushButton("Open results folder")
        folder.clicked.connect(self._open_folder)
        toolbar.addWidget(folder)
        layout.addLayout(toolbar)

        presets = QHBoxLayout()
        for label, key in (
            ("Essential", "essential"),
            ("Counts", "counts"),
            ("Physical metrics", "physical"),
            ("Percentages", "percent"),
            ("Show all", "all"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, preset=key: self._apply_preset(preset))
            presets.addWidget(button)
        choose = QPushButton("Choose columns…")
        choose.clicked.connect(self._choose_columns)
        presets.addWidget(choose)
        self.export_button = QPushButton("Export visible CSV")
        self.export_button.clicked.connect(self._export)
        presets.addWidget(self.export_button)
        presets.addStretch()
        layout.addLayout(presets)

        self.tabs = QTabWidget()
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tabs.addTab(self.table, "Regional table")

        self.overlay_label = QLabel("No atlas overlay is available for the selected result.")
        self.overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_label.setMinimumSize(320, 220)
        self.overlay_scroll = QScrollArea()
        self.overlay_scroll.setWidgetResizable(False)
        self.overlay_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_scroll.setWidget(self.overlay_label)
        self.tabs.addTab(self.overlay_scroll, "Atlas overlay")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel("Run Analyze to create a regional CSV preview.")
        self.status.setProperty("muted", True)
        layout.addWidget(self.status)
        self._update_actions()

    def set_project(self, project_path: Path | None) -> None:
        """Select a project without eagerly constructing a large result table."""
        self.project_path = project_path
        self.loaded_path = None
        self.headers = []
        self.rows = []
        self.visible = {}
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.overlay_label.setPixmap(QPixmap())
        self.overlay_label.setText("Open Results to load the matching atlas overlay.")
        self._discover_files()

    def set_busy(self, busy: bool) -> None:
        """Prevent desktop QuPath launch while a headless job owns the project."""
        self.busy = busy
        self._update_actions()

    def refresh(self) -> None:
        """Discover result files and retain the current choice when possible."""
        self._discover_files()
        if self.file_combo.count():
            self._load_selected()

    def ensure_loaded(self) -> None:
        """Load the selected result once when the Results page becomes visible."""
        selected = self.paths.get(self.file_combo.currentText())
        if selected is not None and selected != self.loaded_path:
            self._load_selected()

    def _discover_files(self) -> None:
        """Populate the lightweight result-file selector without parsing CSV rows."""
        current = self.file_combo.currentText()
        paths = discover_regional_csvs(self.project_path)
        self.paths = {path.name: path for path in paths}
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        self.file_combo.addItems(list(self.paths))
        if current in self.paths:
            self.file_combo.setCurrentText(current)
        self.file_combo.blockSignals(False)
        if not self.file_combo.count():
            self.headers = []
            self.rows = []
            self.visible = {}
            self.loaded_path = None
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.overlay_label.setPixmap(QPixmap())
            self.overlay_label.setText("No atlas overlay is available for this project.")
            self.status.setText("No regional CSV files were found for this project.")
            self._update_actions()
        else:
            self.status.setText("Choose Results or press Refresh to load the selected regional CSV.")
            self._update_actions()

    def _load_selected(self, _name: str = "") -> None:
        path = self.paths.get(self.file_combo.currentText())
        if path is None:
            return
        try:
            self.headers, self.rows, truncated = read_preview_csv(path, row_limit=1500)
        except (OSError, ValueError, csv.Error) as exc:
            QMessageBox.critical(self, "Results preview", f"Could not read the selected CSV:\n{exc}")
            return
        self.loaded_path = path
        self.visible = {header: False for header in self.headers}
        self._apply_preset("essential")
        self._load_overlay(path)
        suffix = "; preview limited to 1,500 rows" if truncated else ""
        self.status.setText(f"{len(self.rows):,} rows loaded from {path.name}{suffix}.")
        self._update_actions()

    def _load_overlay(self, result_path: Path) -> None:
        """Load a bounded preview of the exact version-matched atlas overlay."""
        overlay = atlas_overlay_for_result(result_path)
        self.overlay_label.setPixmap(QPixmap())
        if overlay is None:
            self.overlay_label.setText("No version-matched atlas overlay was found for this CSV.")
            self.tabs.setTabText(1, "Atlas overlay — unavailable")
            return
        reader = QImageReader(str(overlay))
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > 1600:
            scale = 1600.0 / max(size.width(), size.height())
            reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
        image = reader.read()
        if image.isNull():
            self.overlay_label.setText(f"Could not decode atlas overlay:\n{reader.errorString()}")
            self.tabs.setTabText(1, "Atlas overlay — image error")
            return
        self.overlay_label.setText("")
        self.overlay_label.setPixmap(QPixmap.fromImage(image))
        self.overlay_label.adjustSize()
        self.tabs.setTabText(1, "Atlas overlay")

    def _selected_columns(self) -> list[str]:
        return [header for header in self.headers if self.visible.get(header, False)]

    def _apply_preset(self, preset: str) -> None:
        for header in self.headers:
            selected = header in IDENTIFIER_COLUMNS
            if preset == "all":
                selected = True
            elif preset == "essential":
                selected = header in ESSENTIAL_IDENTIFIER_COLUMNS
                selected = selected or header == "Region_Area_mm2"
                selected = selected or any(hint in header for hint in COUNT_HINTS)
                selected = selected and "Density" not in header and "Percent" not in header
            elif preset == "counts":
                selected = (selected or any(hint in header for hint in COUNT_HINTS))
                selected = selected and "Density" not in header and "Percent" not in header
            elif preset == "physical":
                selected = selected or any(hint in header for hint in PHYSICAL_HINTS)
            elif preset == "percent":
                selected = selected or PERCENT_HINT in header
            self.visible[header] = selected
        self._render()

    def _choose_columns(self) -> None:
        if not self.headers:
            QMessageBox.information(self, "Choose columns", "Load a regional CSV first.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Choose visible result columns")
        dialog.resize(720, 600)
        outer = QVBoxLayout(dialog)
        outer.addWidget(QLabel("Tick measurements to display and include in Export visible CSV."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        choices = QVBoxLayout(content)
        checkboxes: dict[str, QCheckBox] = {}
        for header in self.headers:
            checkbox = QCheckBox(header.replace("_", " "))
            checkbox.setChecked(self.visible.get(header, False))
            checkboxes[header] = checkbox
            choices.addWidget(checkbox)
        choices.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for header, checkbox in checkboxes.items():
                self.visible[header] = checkbox.isChecked()
            self._render()

    def _render(self) -> None:
        columns = self._selected_columns()
        self.table.setUpdatesEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([column.replace("_", " ") for column in columns])
        self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            for column_index, column in enumerate(columns):
                item = QTableWidgetItem(row.get(column, ""))
                if any(token in column for token in ("Count", "Positive", "Area", "Density", "Percent", "Pixel_Size")):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, column_index, item)
        for column_index, column in enumerate(columns):
            if column == "Image":
                width = 190
            elif column in {"Region_Name", "Major_Group"}:
                width = 210
            elif column in {"Acronym", "Hemisphere"}:
                width = 105
            else:
                width = 135
            self.table.setColumnWidth(column_index, width)
        self.table.setUpdatesEnabled(True)

    def _export(self) -> None:
        source = self.paths.get(self.file_combo.currentText())
        columns = self._selected_columns()
        if source is None or not columns:
            QMessageBox.information(self, "Export visible CSV", "Select at least one result column.")
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export selected result columns",
            str(source.with_name(f"{source.stem}_selected.csv")),
            "CSV file (*.csv)",
        )
        if not destination:
            return
        try:
            count = export_selected_columns(source, Path(destination), columns)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export visible CSV", f"Could not export the compact CSV:\n{exc}")
            return
        QMessageBox.information(self, "Export visible CSV", f"Exported {count:,} rows and {len(columns)} columns.")

    def _open_in_qupath(self) -> None:
        if self.project_path is None:
            return
        image_name = result_image_entry(self.rows)
        if not image_name:
            QMessageBox.information(self, "Open in QuPath", "The selected result does not identify exactly one image entry.")
            return
        self.open_qupath(self.project_path, image_name)

    def _open_folder(self) -> None:
        if self.project_path is None:
            return
        folder = self.project_path.parent / "brain_cell_counter_results"
        folder.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _update_actions(self) -> None:
        loaded = bool(self.rows and self.headers)
        self.export_button.setEnabled(loaded)
        self.open_qupath_button.setEnabled(loaded and not self.busy and result_image_entry(self.rows) is not None)
