"""Preview versioned regional CSV results with user-selectable table columns.

The preview is read-only: checkbox and preset changes affect display state only
and never rewrite scientific result files.
"""

from __future__ import annotations

import csv
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


COUNT_HINTS = ("DAPI", "Positive", "+")
PHYSICAL_HINTS = ("Area_mm2", "Density_per_mm2", "Pixel_Size")
PERCENT_HINT = "Percent"
IDENTIFIER_COLUMNS = {
    "Image",
    "Region_ID",
    "Acronym",
    "Region_Name",
    "Major_Group",
    "Functional_Subregion",
    "Hierarchy_Level",
    "Hemisphere",
}
ESSENTIAL_IDENTIFIER_COLUMNS = {
    "Image",
    "Region_ID",
    "Acronym",
    "Region_Name",
    "Major_Group",
    "Functional_Subregion",
    "Hemisphere",
}


def discover_regional_csvs(project_path: Path | None) -> list[Path]:
    """Return all compatible regional CSVs with the newest file first."""
    if project_path is None:
        return []
    output_directory = project_path.parent / "brain_cell_counter_results"
    if not output_directory.is_dir():
        return []
    return sorted(
        output_directory.glob("*_regional_counts_v*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def atlas_overlay_for_result(result_path: Path) -> Path | None:
    """Return the exact version-matched atlas overlay for a regional CSV."""
    path = Path(result_path)
    marker = "_regional_counts_"
    if marker not in path.stem:
        return None
    image_prefix, version_suffix = path.stem.rsplit(marker, 1)
    candidate = path.with_name(f"{image_prefix}_atlas_overlay_{version_suffix}.png")
    return candidate if candidate.is_file() else None


def read_preview_csv(path: Path, row_limit: int = 5000) -> tuple[list[str], list[dict[str, str]], bool]:
    """Read a bounded CSV preview and report whether additional rows were omitted."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = list(reader.fieldnames or [])
        rows: list[dict[str, str]] = []
        truncated = False
        for index, row in enumerate(reader):
            if index >= row_limit:
                truncated = True
                break
            rows.append({header: row.get(header, "") or "" for header in headers})
    return headers, rows, truncated


def result_image_entry(rows: list[dict[str, str]]) -> str | None:
    """Return the one exact QuPath image name represented by a result table."""
    image_names = {
        row.get("Image", "").strip()
        for row in rows
        if row.get("Image", "").strip()
    }
    if len(image_names) != 1:
        return None
    return next(iter(image_names))


def export_selected_columns(source: Path, destination: Path, columns: list[str]) -> int:
    """Write all result rows with only the user-selected columns."""
    if not columns:
        raise ValueError("Select at least one output column.")
    if source.resolve() == destination.resolve():
        raise ValueError("Choose a new filename; the master result cannot be overwritten.")
    with source.open("r", encoding="utf-8-sig", newline="") as input_stream:
        reader = csv.DictReader(input_stream)
        available = set(reader.fieldnames or [])
        missing = [column for column in columns if column not in available]
        if missing:
            raise ValueError(f"Columns are missing from the source CSV: {', '.join(missing)}")
        with destination.open("w", encoding="utf-8-sig", newline="") as output_stream:
            writer = csv.DictWriter(output_stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            row_count = 0
            for row in reader:
                writer.writerow(row)
                row_count += 1
    return row_count


class ResultsPreviewTab(ttk.Frame):
    """Render result-file selection, measurement checkboxes, and a read-only table."""
    def __init__(self, parent: tk.Widget, open_results_callback, open_qupath_callback) -> None:
        super().__init__(parent)
        self.open_results_callback = open_results_callback
        self.open_qupath_callback = open_qupath_callback
        self.project_path: Path | None = None
        self.csv_paths: dict[str, Path] = {}
        self.headers: list[str] = []
        self.rows: list[dict[str, str]] = []
        self.column_vars: dict[str, tk.BooleanVar] = {}
        self.file_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Run ANALYZE to create a regional CSV preview.")
        self.busy = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        ttk.Label(self, text="Results preview", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            self,
            text="Choose a result file, preview measurements, or open its saved detections directly in QuPath.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 10))

        toolbar = ttk.Frame(self)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="Regional CSV").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.file_combo = ttk.Combobox(toolbar, textvariable=self.file_var, state="readonly")
        self.file_combo.grid(row=0, column=1, sticky="ew")
        self.file_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_selected_file())
        ttk.Button(toolbar, text="Refresh", command=self.refresh).grid(row=0, column=2, padx=(8, 0))
        self.qupath_button = ttk.Button(
            toolbar,
            text="OPEN IN QUPATH",
            command=self._open_in_qupath,
            state="disabled",
        )
        self.qupath_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Button(toolbar, text="Open results folder", command=self.open_results_callback).grid(
            row=0, column=4, padx=(8, 0)
        )

        content = ttk.Panedwindow(self, orient="vertical")
        content.grid(row=3, column=0, sticky="nsew")

        chooser = ttk.LabelFrame(content, text="Result columns", padding=10)
        self.chooser = chooser
        content.add(chooser, weight=0)

        preset_bar = ttk.Frame(chooser)
        preset_bar.grid(row=0, column=0, sticky="w")
        ttk.Button(preset_bar, text="Essential", command=lambda: self._apply_preset("essential")).pack(side="left")
        ttk.Button(preset_bar, text="Counts", command=lambda: self._apply_preset("counts")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(preset_bar, text="Physical metrics", command=lambda: self._apply_preset("physical")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(preset_bar, text="Percentages", command=lambda: self._apply_preset("percent")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(preset_bar, text="Show all", command=lambda: self._apply_preset("all")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(preset_bar, text="Choose columns…", command=self._show_column_chooser).pack(
            side="left", padx=(16, 0)
        )
        self.export_button = ttk.Button(
            preset_bar,
            text="EXPORT VISIBLE CSV",
            command=self._export_visible_csv,
            state="disabled",
        )
        self.export_button.pack(side="left", padx=(16, 0))

        table_frame = ttk.Frame(content, width=920, height=250)
        # Keep a compact viewport; the horizontal scrollbar exposes wide tables.
        table_frame.grid_propagate(False)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        content.add(table_frame, weight=1)
        self.table = ttk.Treeview(table_frame, show="headings")
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        ttk.Label(self, textvariable=self.status_var, style="Subtitle.TLabel").grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )

    def set_project(self, project_path: Path | None) -> None:
        self.project_path = project_path
        self.refresh()

    def refresh(self) -> None:
        paths = discover_regional_csvs(self.project_path)
        self.csv_paths = {path.name: path for path in paths}
        names = list(self.csv_paths)
        self.file_combo["values"] = names
        if not names:
            self.file_var.set("")
            self._clear_preview()
            self.qupath_button.configure(state="disabled")
            self.export_button.configure(state="disabled")
            self.status_var.set("No regional CSV files were found for the selected project.")
            return
        current = self.file_var.get()
        self.file_var.set(current if current in self.csv_paths else names[0])
        self._load_selected_file()

    def _load_selected_file(self) -> None:
        path = self.csv_paths.get(self.file_var.get())
        if path is None:
            return
        try:
            self.headers, self.rows, truncated = read_preview_csv(path)
        except (OSError, csv.Error) as exc:
            messagebox.showerror("Results preview", f"Could not read the selected CSV:\n{exc}", parent=self)
            return
        self._rebuild_column_choices()
        self._apply_preset("essential")
        self._update_qupath_button_state()
        self.export_button.configure(state="normal")
        suffix = "; preview limited to 5,000 rows" if truncated else ""
        self.status_var.set(f"{len(self.rows):,} rows loaded from {path.name}{suffix}.")

    def _open_in_qupath(self) -> None:
        """Ask the application controller to open the result entry in QuPath."""
        image_name = result_image_entry(self.rows)
        if self.project_path is None or not image_name:
            messagebox.showinfo(
                "Open in QuPath",
                "The selected result does not identify exactly one QuPath image entry.",
                parent=self,
            )
            return
        self.open_qupath_callback(self.project_path, image_name)

    def set_busy(self, busy: bool) -> None:
        """Prevent QuPath desktop launch while a headless project job is active."""
        self.busy = busy
        self._update_qupath_button_state()

    def _update_qupath_button_state(self) -> None:
        """Enable launch only for one known entry in an idle selected project."""
        image_name = result_image_entry(self.rows)
        ready = self.project_path is not None and image_name is not None and not self.busy
        self.qupath_button.configure(state="normal" if ready else "disabled")

    def _clear_preview(self) -> None:
        self.headers = []
        self.rows = []
        self.column_vars.clear()
        self.table.delete(*self.table.get_children())
        self.table["columns"] = ()

    def _visible_columns(self) -> list[str]:
        """Return selected columns in their original source order."""
        return [
            header
            for header in self.headers
            if header in self.column_vars and self.column_vars[header].get()
        ]

    def _export_visible_csv(self) -> None:
        """Create a compact CSV without modifying the complete master result."""
        source = self.csv_paths.get(self.file_var.get())
        if source is None:
            return
        columns = self._visible_columns()
        if not columns:
            messagebox.showinfo("Export visible CSV", "Select at least one result column.", parent=self)
            return
        destination_text = filedialog.asksaveasfilename(
            parent=self,
            title="Export selected result columns",
            initialdir=str(source.parent),
            initialfile=f"{source.stem}_selected.csv",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv")],
        )
        if not destination_text:
            return
        destination = Path(destination_text)
        try:
            row_count = export_selected_columns(source, destination, columns)
        except (OSError, csv.Error, ValueError) as exc:
            messagebox.showerror("Export visible CSV", f"Could not export the compact CSV:\n{exc}", parent=self)
            return
        messagebox.showinfo(
            "Export visible CSV",
            f"Exported {row_count:,} rows and {len(columns)} columns to:\n{destination}",
            parent=self,
        )

    def _rebuild_column_choices(self) -> None:
        self.column_vars = {header: tk.BooleanVar(value=False) for header in self.headers}

    def _show_column_chooser(self) -> None:
        """Open a scrollable selector without widening the main results tab."""
        if not self.headers:
            messagebox.showinfo("Choose columns", "Load a regional CSV first.", parent=self)
            return
        window = tk.Toplevel(self)
        window.title("Choose visible result columns")
        window.geometry("700x560")
        window.minsize(560, 420)
        window.transient(self.winfo_toplevel())

        outer = ttk.Frame(window, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text="Tick the measurements to display and include in EXPORT VISIBLE CSV.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        choices = ttk.Frame(canvas)
        choices.columnconfigure(0, weight=1)
        choices.columnconfigure(1, weight=1)
        canvas_window = canvas.create_window((0, 0), window=choices, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def resize_choices(_event: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def resize_window(event: tk.Event) -> None:
            canvas.itemconfigure(canvas_window, width=event.width)

        choices.bind("<Configure>", resize_choices)
        canvas.bind("<Configure>", resize_window)
        for index, header in enumerate(self.headers):
            ttk.Checkbutton(
                choices,
                text=header.replace("_", " "),
                variable=self.column_vars[header],
                command=self._render_table,
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 18), pady=3)

        ttk.Button(window, text="Done", command=window.destroy).pack(pady=(0, 12))

    def _apply_preset(self, preset: str) -> None:
        for header, variable in self.column_vars.items():
            selected = header in IDENTIFIER_COLUMNS
            if preset == "all":
                selected = True
            elif preset == "essential":
                selected = header in ESSENTIAL_IDENTIFIER_COLUMNS
                selected = selected or header == "Region_Area_mm2"
                selected = selected or any(hint in header for hint in COUNT_HINTS)
                selected = selected and "Density" not in header and "Percent" not in header
            elif preset == "counts":
                selected = selected or any(hint in header for hint in COUNT_HINTS)
                selected = selected and "Density" not in header and "Percent" not in header
            elif preset == "physical":
                selected = selected or any(hint in header for hint in PHYSICAL_HINTS)
            elif preset == "percent":
                selected = selected or PERCENT_HINT in header
            variable.set(selected)
        self._render_table()

    def _render_table(self) -> None:
        visible = self._visible_columns()
        self.table.delete(*self.table.get_children())
        self.table["columns"] = visible
        for header in visible:
            self.table.heading(header, text=header.replace("_", " "))
            width = 170 if header in {"Image", "Region_Name", "Major_Group", "Functional_Subregion"} else 125
            self.table.column(header, width=width, minwidth=80, stretch=False, anchor="e" if self._numeric(header) else "w")
        for row in self.rows:
            self.table.insert("", "end", values=[row.get(header, "") for header in visible])

    @staticmethod
    def _numeric(header: str) -> bool:
        return any(token in header for token in ("Count", "Positive", "Area", "Density", "Percent", "Pixel_Size"))
