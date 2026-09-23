"""Provide the human-reviewed, sequential batch calibration user interface.

The wizard owns one QuPath subprocess at a time, never overwrites source TIFFs,
and prevents unconfirmed scale proposals from changing project metadata.
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .calibration import (
    CalibrationRecord,
    backup_project_calibration_targets,
    detect_scale_bar,
    is_supported_image,
    match_records_to_project,
    next_fiji_output_path,
    valid_confirmed_fiji_records,
    valid_confirmed_records,
    write_calibration_audit,
    write_calibration_manifest,
)


class CalibrationWizard(tk.Toplevel):
    """Batch scale-bar review with explicit human confirmation before writes."""

    BG = "#F7F7F8"
    SURFACE = "#FFFFFF"
    BORDER = "#E5E5E5"
    TEXT = "#202123"
    MUTED = "#6B6B6B"
    PRIMARY = "#202123"
    ACCENT = "#10A37F"
    ACCENT_DARK = "#0D8A6C"
    WARNING = "#B45309"
    ERROR = "#B42318"

    def __init__(
        self,
        parent: tk.Misc,
        qupath_path: Path,
        fiji_path: Path,
        project_path: Path,
        busy_callback,
        *,
        standalone: bool = False,
    ) -> None:
        super().__init__(parent)
        self.title("Batch image calibration — human review required")
        self.geometry("1180x760")
        self.minsize(940, 620)
        # The classic Tk application uses a transient child window.  The Qt
        # launcher instead hides its small Tk owner, so making this window
        # transient there would also keep Calibration invisible on Windows.
        if not standalone:
            self.transient(parent)
        self.qupath_path = qupath_path
        self.fiji_path = fiji_path
        self.project_path = project_path
        self.busy_callback = busy_callback
        self.records: list[CalibrationRecord] = []
        self.events: queue.Queue = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.running = False
        self.stop_requested = False
        self.preview_script = (
            Path(__file__).resolve().parent.parent / "qupath_scripts" / "Brain_Cell_Counter_Calibration_Preview_v1.0.groovy"
        )
        self.apply_script = (
            Path(__file__).resolve().parent.parent / "qupath_scripts" / "Brain_Cell_Counter_Calibration_Apply_v1.0.groovy"
        )
        self.fiji_script = (
            Path(__file__).resolve().parent.parent / "qupath_scripts" / "Brain_Cell_Counter_Fiji_Calibration_v1.0.groovy"
        )
        self.output_directory = self.project_path.parent / "brain_cell_counter_calibration"
        self.qc_directory = self.output_directory / "qc"
        self.fiji_output_directory = self.output_directory / "fiji_calibrated"
        self.fiji_report_directory = self.output_directory / "fiji_verification"
        self._configure_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._drain_events)

    def _configure_styles(self) -> None:
        """Apply the same restrained light visual language as the Qt shell."""
        self.configure(background=self.BG)
        self.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("App.TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.SURFACE, borderwidth=1, relief="solid")
        style.configure("Header.TLabel", background=self.BG, foreground=self.TEXT, font=("Segoe UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=self.BG, foreground=self.MUTED, font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=self.SURFACE, foreground=self.TEXT, font=("Segoe UI", 11, "bold"))
        style.configure("CardText.TLabel", background=self.SURFACE, foreground=self.MUTED, font=("Segoe UI", 9))
        style.configure("Metric.TLabel", background=self.SURFACE, foreground=self.TEXT, font=("Segoe UI", 18, "bold"))
        style.configure("MetricName.TLabel", background=self.SURFACE, foreground=self.MUTED, font=("Segoe UI", 9))
        style.configure("Status.TLabel", background="#E8F7F2", foreground="#08765B", font=("Segoe UI", 9, "bold"), padding=(10, 5))
        style.configure("Primary.TButton", background=self.PRIMARY, foreground="#FFFFFF", borderwidth=0, padding=(14, 9), font=("Segoe UI", 9, "bold"))
        style.map("Primary.TButton", background=[("active", "#343541"), ("pressed", "#111111"), ("disabled", "#B7B7B7")])
        style.configure("Accent.TButton", background=self.ACCENT, foreground="#FFFFFF", borderwidth=0, padding=(14, 9), font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", self.ACCENT_DARK), ("pressed", "#08765B"), ("disabled", "#A8DCCF")])
        style.configure("Secondary.TButton", background=self.SURFACE, foreground=self.TEXT, bordercolor=self.BORDER, borderwidth=1, padding=(12, 8))
        style.map("Secondary.TButton", background=[("active", "#F1F1F1"), ("pressed", "#E8E8E8"), ("disabled", "#F5F5F5")])
        style.configure("Danger.TButton", background="#FFF1F0", foreground=self.ERROR, bordercolor="#FECDCA", borderwidth=1, padding=(12, 8))
        style.map("Danger.TButton", background=[("active", "#FEE4E2"), ("disabled", "#F7F7F8")])
        style.configure("Calibration.Treeview", background=self.SURFACE, fieldbackground=self.SURFACE, foreground=self.TEXT, rowheight=32, borderwidth=0)
        style.configure("Calibration.Treeview.Heading", background="#F3F4F6", foreground=self.TEXT, borderwidth=0, padding=(8, 8), font=("Segoe UI", 9, "bold"))
        style.map("Calibration.Treeview", background=[("selected", "#DDF3ED")], foreground=[("selected", self.TEXT)])
        style.configure("Calibration.Horizontal.TProgressbar", troughcolor="#ECECEC", background=self.ACCENT, borderwidth=0, thickness=8)

    @staticmethod
    def _center_window(window: tk.Misc, requested_width: int, requested_height: int) -> None:
        """Fit a window to the active monitor and place it near the center."""
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        width = min(requested_width, max(940, screen_width - 80))
        height = min(requested_height, max(620, screen_height - 100))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2 - 20)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _metric_card(self, parent: tk.Misc, title: str, variable: tk.StringVar) -> ttk.Frame:
        """Create one compact live batch-summary card."""
        card = ttk.Frame(parent, style="Card.TFrame", padding=(16, 12))
        ttk.Label(card, textvariable=variable, style="Metric.TLabel").pack(anchor="w")
        ttk.Label(card, text=title, style="MetricName.TLabel").pack(anchor="w", pady=(1, 0))
        return card

    def _build_ui(self) -> None:
        self._center_window(self, 1240, 850)
        root = ttk.Frame(self, style="App.TFrame", padding=(24, 20, 24, 18))
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root, style="App.TFrame")
        header.pack(fill="x")
        title_area = ttk.Frame(header, style="App.TFrame")
        title_area.pack(side="left", fill="x", expand=True)
        ttk.Label(title_area, text="Calibration", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            title_area,
            text="Human-reviewed scale recovery for stitched microscopy images",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(header, text="HUMAN REVIEW REQUIRED", style="Status.TLabel").pack(side="right", anchor="n", pady=(4, 0))

        workflow = ttk.Frame(root, style="App.TFrame")
        workflow.pack(fill="x", pady=(18, 14))
        step_text = (
            ("1  Add images", "Choose files, a folder, or all project entries."),
            ("2  Scan & review", "Verify both endpoints and the printed distance."),
            ("3  Export or apply", "Create verified copies or backed-up metadata."),
        )
        for column, (title, description) in enumerate(step_text):
            card = ttk.Frame(workflow, style="Card.TFrame", padding=(14, 10))
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 0 if column == 2 else 5))
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(card, text=description, style="CardText.TLabel").pack(anchor="w", pady=(2, 0))
            workflow.columnconfigure(column, weight=1)

        summary = ttk.Frame(root, style="App.TFrame")
        summary.pack(fill="x", pady=(0, 14))
        self.total_metric = tk.StringVar(value="0")
        self.scanned_metric = tk.StringVar(value="0")
        self.confirmed_metric = tk.StringVar(value="0")
        self.matched_metric = tk.StringVar(value="0")
        for column, (title, variable) in enumerate((
            ("Images", self.total_metric),
            ("Scanned", self.scanned_metric),
            ("Confirmed", self.confirmed_metric),
            ("Project matched", self.matched_metric),
        )):
            card = self._metric_card(summary, title, variable)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 0 if column == 3 else 5))
            summary.columnconfigure(column, weight=1)

        table_card = ttk.Frame(root, style="Card.TFrame", padding=(16, 14))
        table_card.pack(fill="both", expand=True)
        table_header = ttk.Frame(table_card, style="Card.TFrame")
        table_header.pack(fill="x", pady=(0, 10))
        heading = ttk.Frame(table_header, style="Card.TFrame")
        heading.pack(side="left", fill="x", expand=True)
        ttk.Label(heading, text="Calibration batch", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(heading, text="Double-click a scanned row to review its scale bar.", style="CardText.TLabel").pack(anchor="w", pady=(2, 0))
        toolbar = ttk.Frame(table_header, style="Card.TFrame")
        toolbar.pack(side="right")
        self.add_files_button = ttk.Button(toolbar, text="Add images…", command=self._add_files, style="Secondary.TButton")
        self.add_files_button.pack(side="left")
        self.add_folder_button = ttk.Button(toolbar, text="Add folder…", command=self._add_folder, style="Secondary.TButton")
        self.add_folder_button.pack(side="left", padx=6)
        self.add_project_button = ttk.Button(toolbar, text="Add project images", command=self._add_project_images, style="Secondary.TButton")
        self.add_project_button.pack(side="left")
        self.remove_button = ttk.Button(toolbar, text="Remove", command=self._remove_selected, style="Danger.TButton")
        self.remove_button.pack(side="left", padx=(6, 0))

        columns = (
            "file",
            "project",
            "metadata",
            "bar",
            "distance",
            "proposal",
            "confidence",
            "status",
        )
        table_frame = ttk.Frame(table_card, style="Card.TFrame")
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended", style="Calibration.Treeview")
        headings = {
            "file": "File",
            "project": "QuPath entry",
            "metadata": "Metadata µm/px",
            "bar": "Bar px",
            "distance": "Known distance",
            "proposal": "Proposed µm/px",
            "confidence": "Detection",
            "status": "Status",
        }
        widths = {"file": 220, "project": 170, "metadata": 105, "bar": 75, "distance": 105, "proposal": 110, "confidence": 85, "status": 125}
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=widths[column], anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal_scrollbar.set)
        self.table.bind("<Double-1>", lambda _event: self._review_selected())
        self.table.bind("<<TreeviewSelect>>", lambda _event: self._update_action_states())
        self.table.tag_configure("complete", background="#ECFDF3", foreground="#08765B")
        self.table.tag_configure("review", background="#FFFAEB", foreground="#93370D")
        self.table.tag_configure("attention", background="#FEF3F2", foreground="#B42318")

        actions = ttk.Frame(root, style="App.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        self.scan_button = ttk.Button(actions, text="Scan scale bars", command=self._start_scan, style="Primary.TButton")
        self.scan_button.pack(side="left")
        self.review_button = ttk.Button(actions, text="Review selected", command=self._review_selected, style="Primary.TButton")
        self.review_button.pack(side="left", padx=(6, 0))
        self.export_button = ttk.Button(actions, text="Export manifest", command=self._export_manifest, style="Secondary.TButton")
        self.export_button.pack(side="left", padx=6)
        self.apply_button = ttk.Button(
            actions,
            text="Apply confirmed to QuPath",
            command=self._start_apply,
            style="Secondary.TButton",
        )
        self.apply_button.pack(side="left")
        self.fiji_button = ttk.Button(
            actions,
            text="Export calibrated with Fiji",
            command=self._start_fiji_export,
            style="Accent.TButton",
        )
        self.fiji_button.pack(side="left", padx=(6, 0))
        self.stop_button = ttk.Button(actions, text="Stop", command=self._stop, state="disabled", style="Danger.TButton")
        self.stop_button.pack(side="left", padx=6)
        ttk.Button(actions, text="Close", command=self._close, style="Secondary.TButton").pack(side="right")

        self.progress_value = tk.DoubleVar(value=0.0)
        self.progress_text = tk.StringVar(value="Ready")
        activity = ttk.Frame(root, style="Card.TFrame", padding=(16, 12))
        activity.pack(fill="x", pady=(14, 0))
        ttk.Label(activity, text="Activity", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(activity, textvariable=self.progress_text, style="CardText.TLabel").pack(fill="x", pady=(4, 5))
        ttk.Progressbar(activity, maximum=100, variable=self.progress_value, style="Calibration.Horizontal.TProgressbar").pack(fill="x")
        self.log = tk.Text(
            activity,
            height=5,
            wrap="word",
            state="disabled",
            background="#FAFAFA",
            foreground=self.TEXT,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            padx=10,
            pady=8,
            font=("Consolas", 9),
        )
        self.log.pack(fill="x", pady=(10, 0))
        self._update_action_states()

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.running = running
        if running:
            for button in (
                self.add_files_button,
                self.add_folder_button,
                self.add_project_button,
                self.remove_button,
                self.scan_button,
                self.review_button,
                self.export_button,
                self.apply_button,
                self.fiji_button,
            ):
                button.configure(state="disabled")
            self.stop_button.configure(state="normal")
        else:
            self.stop_button.configure(state="disabled")
            self._update_action_states()
        self.busy_callback(running)

    def _update_action_states(self) -> None:
        """Enable only actions supported by the current reviewed batch state."""
        if self.running:
            return
        has_records = bool(self.records)
        selection = self._selected_index() if hasattr(self, "table") else None
        selected_scanned = selection is not None and bool(self.records[selection].preview_path)
        any_scanned = any(record.preview_path for record in self.records)
        self.add_files_button.configure(state="normal")
        self.add_folder_button.configure(state="normal")
        self.add_project_button.configure(state="normal")
        self.remove_button.configure(state="normal" if self.table.selection() else "disabled")
        self.scan_button.configure(state="normal" if has_records else "disabled")
        self.review_button.configure(state="normal" if selected_scanned or any_scanned else "disabled")
        self.export_button.configure(state="normal" if has_records else "disabled")
        self.apply_button.configure(state="normal" if valid_confirmed_records(self.records) else "disabled")
        self.fiji_button.configure(state="normal" if valid_confirmed_fiji_records(self.records) else "disabled")

    def _add_paths(self, paths: list[Path]) -> None:
        existing = {str(Path(record.source_path).resolve()).casefold() for record in self.records}
        added = 0
        for path in paths:
            if not is_supported_image(path):
                continue
            resolved = str(path.resolve()).casefold()
            if resolved in existing:
                continue
            self.records.append(CalibrationRecord(source_path=str(path.resolve())))
            existing.add(resolved)
            added += 1
        match_records_to_project(self.records, self.project_path)
        self._refresh_table()
        self._append_log(f"Added {added} image(s). Total: {len(self.records)}.")

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Add stitched images for calibration",
            filetypes=[
                ("Microscopy images", "*.tif *.tiff *.ome.tif *.ome.tiff *.png *.jpg *.jpeg"),
                ("All files", "*.*"),
            ],
        )
        self._add_paths([Path(path) for path in paths])

    def _add_folder(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Add images from folder")
        if not selected:
            return
        root = Path(selected)
        paths = [path for path in root.rglob("*") if is_supported_image(path)]
        if len(paths) > 100 and not messagebox.askyesno(
            "Large calibration batch",
            f"The folder contains {len(paths)} supported images. Add all of them?",
            parent=self,
        ):
            return
        self._add_paths(paths)

    def _add_project_images(self) -> None:
        from .calibration import read_project_source_entries

        entries = read_project_source_entries(self.project_path)
        paths = [entry["source_path"] for entry in entries if isinstance(entry.get("source_path"), Path)]
        self._add_paths(paths)

    def _remove_selected(self) -> None:
        indexes = sorted((int(item) for item in self.table.selection()), reverse=True)
        for index in indexes:
            if 0 <= index < len(self.records):
                self.records.pop(index)
        self._refresh_table()

    def _refresh_table(self) -> None:
        selection_paths = {
            self.records[int(item)].source_path
            for item in self.table.selection()
            if str(item).isdigit() and int(item) < len(self.records)
        }
        for item in self.table.get_children():
            self.table.delete(item)
        for index, record in enumerate(self.records):
            metadata = ""
            if record.metadata_pixel_width_microns is not None:
                metadata = f"{record.metadata_pixel_width_microns:.6g}"
            bar = f"{record.bar_preview_length_pixels:.1f}" if record.bar_preview_length_pixels else ""
            distance = (
                f"{record.known_distance:g} {record.known_unit}" if record.known_distance is not None else ""
            )
            proposal = (
                f"{record.proposed_pixel_size_microns:.6g}"
                if record.proposed_pixel_size_microns is not None
                else ""
            )
            confidence = (
                f"{record.detection_confidence * 100:.0f}%"
                if record.detection_confidence is not None
                else ""
            )
            if record.fiji_verified:
                status = "Fiji verified"
            elif record.applied:
                status = "Applied"
            elif record.confirmed:
                status = "Confirmed"
            elif record.preview_path:
                status = "Review required"
            elif record.note:
                status = "Needs attention"
            else:
                status = "Not scanned"
            if record.fiji_verified or record.applied or record.confirmed:
                row_tag = "complete"
            elif record.preview_path:
                row_tag = "review"
            elif record.note:
                row_tag = "attention"
            else:
                row_tag = ""
            self.table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    Path(record.source_path).name,
                    record.image_name or "Not matched",
                    metadata,
                    bar,
                    distance,
                    proposal,
                    confidence,
                    status,
                ),
                tags=(row_tag,) if row_tag else (),
            )
            if record.source_path in selection_paths:
                self.table.selection_add(str(index))
        self.total_metric.set(str(len(self.records)))
        self.scanned_metric.set(str(sum(bool(record.preview_path) for record in self.records)))
        self.confirmed_metric.set(str(sum(bool(record.confirmed) for record in self.records)))
        self.matched_metric.set(str(sum(bool(record.image_name) for record in self.records)))
        self._update_action_states()

    @staticmethod
    def _decode_event(line: str) -> dict | None:
        prefix = "BCC_EVENT "
        if not line.startswith(prefix):
            return None
        try:
            event = json.loads(line[len(prefix) :])
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        return event if isinstance(event, dict) else None

    def _preview_command(self, record: CalibrationRecord, output_path: Path) -> list[str]:
        command = [str(self.qupath_path), "-q", "script"]
        if record.image_name:
            command.extend(["-p", str(self.project_path), "-i", record.image_name])
        else:
            command.extend(["-i", record.source_path])
        command.append(str(self.preview_script))
        command.extend(["--args", f"outputPath={output_path.as_posix()}"])
        # A 4000 px preview preserves thin, burned-in scale bars that disappear
        # in ordinary whole-slide thumbnails. QuPath still reads a downsampled
        # level and images are processed one at a time.
        command.extend(["--args", "maxDimension=4000"])
        return command

    def _start_scan(self) -> None:
        if not self.records:
            messagebox.showerror("Calibration", "Add at least one image first.", parent=self)
            return
        if not self.qupath_path.is_file() or not self.preview_script.is_file():
            messagebox.showerror("Calibration", "QuPath or the calibration preview script is missing.", parent=self)
            return
        self.qc_directory.mkdir(parents=True, exist_ok=True)
        self._set_running(True)
        self.stop_requested = False
        self.progress_value.set(0)
        self.progress_text.set("Scanning images sequentially")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        total = len(self.records)
        for index, record in enumerate(self.records):
            if self.stop_requested:
                break
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(record.source_path).stem)[:80]
            output_path = self.qc_directory / f"{index + 1:03d}_{safe_name}_preview.png"
            self.events.put(("log", f"[{index + 1}/{total}] Scanning {Path(record.source_path).name}"))
            payload = None
            tail: list[str] = []
            try:
                self.process = subprocess.Popen(
                    self._preview_command(record, output_path),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                assert self.process.stdout is not None
                for raw_line in self.process.stdout:
                    line = raw_line.rstrip()
                    if line:
                        tail.append(line)
                        tail = tail[-12:]
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
                record.preview_path = str(output_path)
                record.metadata_pixel_width_microns = payload.get("metadataPixelWidth")
                record.metadata_pixel_height_microns = payload.get("metadataPixelHeight")
                candidate = detect_scale_bar(output_path)
                if candidate:
                    record.bar_start_x = candidate.start_x
                    record.bar_start_y = candidate.start_y
                    record.bar_end_x = candidate.end_x
                    record.bar_end_y = candidate.end_y
                    record.bar_preview_length_pixels = candidate.preview_length_pixels
                    record.detection_confidence = candidate.confidence
                    record.note = "Automatic endpoint proposal; human review required."
                else:
                    record.note = "No scale bar candidate detected; select endpoints manually."
                self.events.put(("record", index))
            except Exception as exc:
                record.note = f"Scan failed: {exc}"
                self.events.put(("record", index))
                self.events.put(("log", f"[{index + 1}/{total}] FAILED: {exc}"))
            finally:
                self.process = None
            self.events.put(("progress", ((index + 1) / total * 100.0, f"Scanned {index + 1}/{total}")))
        self.events.put(("scan_done", None))

    def _selected_index(self) -> int | None:
        selection = self.table.selection()
        if not selection:
            return None
        return int(selection[0])

    def _review_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            index = next((i for i, record in enumerate(self.records) if record.preview_path and not record.confirmed), None)
        if index is None:
            messagebox.showinfo("Calibration review", "Select a scanned image to review.", parent=self)
            return
        self._open_review(index)

    def _open_review(self, index: int) -> None:
        record = self.records[index]
        preview_path = Path(record.preview_path)
        if not preview_path.is_file():
            messagebox.showerror("Calibration review", "Scan this image before reviewing it.", parent=self)
            return

        window = tk.Toplevel(self)
        window.title(f"Review calibration {index + 1}/{len(self.records)} — {Path(record.source_path).name}")
        window.configure(background=self.BG)
        self._center_window(window, 1160, 860)
        window.minsize(850, 650)
        window.transient(self)
        window.grab_set()

        review_root = ttk.Frame(window, style="App.TFrame", padding=(22, 18, 22, 18))
        review_root.pack(fill="both", expand=True)
        review_header = ttk.Frame(review_root, style="App.TFrame")
        review_header.pack(fill="x", pady=(0, 14))
        header_text = ttk.Frame(review_header, style="App.TFrame")
        header_text.pack(side="left", fill="x", expand=True)
        ttk.Label(header_text, text="Review scale bar", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header_text,
            text=f"Image {index + 1} of {len(self.records)}  •  {Path(record.source_path).name}",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(review_header, text="CONFIRM MANUALLY", style="Status.TLabel").pack(side="right", anchor="n", pady=(4, 0))

        source_image = Image.open(preview_path).convert("RGB")
        has_candidate = None not in (
            record.bar_start_x,
            record.bar_start_y,
            record.bar_end_x,
            record.bar_end_y,
        )
        if has_candidate:
            x_values = [int(record.bar_start_x), int(record.bar_end_x)]
            y_values = [int(record.bar_start_y), int(record.bar_end_y)]
            left = max(0, min(x_values) - 260)
            right = min(source_image.width, max(x_values) + 261)
            top = max(0, min(y_values) - 180)
            bottom = min(source_image.height, max(y_values) + 181)
            review_image = source_image.crop((left, top, right, bottom))
            offset_x, offset_y = left, top
        else:
            review_image = source_image
            offset_x, offset_y = 0, 0
        display_image = review_image.copy()
        display_image.thumbnail((1080, 570), Image.Resampling.LANCZOS)
        scale_x = display_image.width / review_image.width
        scale_y = display_image.height / review_image.height
        photo = ImageTk.PhotoImage(display_image)
        image_card = ttk.Frame(review_root, style="Card.TFrame", padding=10)
        image_card.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            image_card,
            width=display_image.width,
            height=display_image.height,
            background="#111111",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            cursor="crosshair",
        )
        canvas.pack(expand=True)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.image = photo

        points: list[tuple[float, float]] = []
        if None not in (record.bar_start_x, record.bar_start_y, record.bar_end_x, record.bar_end_y):
            points = [
                (float(record.bar_start_x), float(record.bar_start_y)),
                (float(record.bar_end_x), float(record.bar_end_y)),
            ]

        instruction = tk.StringVar(value="Review the red proposal. Click two endpoints to replace it.")

        def redraw() -> None:
            canvas.delete("scale-proposal")
            if len(points) == 2:
                x1, y1 = points[0]
                x2, y2 = points[1]
                canvas.create_line(
                    (x1 - offset_x) * scale_x,
                    (y1 - offset_y) * scale_y,
                    (x2 - offset_x) * scale_x,
                    (y2 - offset_y) * scale_y,
                    fill="#ff3030",
                    width=3,
                    tags="scale-proposal",
                )
                for x, y in points:
                    canvas.create_oval(
                        (x - offset_x) * scale_x - 6,
                        (y - offset_y) * scale_y - 6,
                        (x - offset_x) * scale_x + 6,
                        (y - offset_y) * scale_y + 6,
                        outline="#ffff00",
                        width=2,
                        tags="scale-proposal",
                    )
                length = math.dist(points[0], points[1])
                instruction.set(f"Selected scale-bar length: {length:.2f} preview pixels. Enter the printed distance and confirm.")
            else:
                instruction.set("Click the first endpoint, then the second endpoint of the scale bar.")

        def canvas_click(event: tk.Event) -> None:
            x = max(0.0, min(source_image.width - 1.0, event.x / scale_x + offset_x))
            y = max(0.0, min(source_image.height - 1.0, event.y / scale_y + offset_y))
            if len(points) >= 2:
                points.clear()
            points.append((x, y))
            redraw()

        canvas.bind("<Button-1>", canvas_click)
        redraw()

        controls = ttk.Frame(review_root, style="Card.TFrame", padding=(16, 12))
        controls.pack(fill="x", pady=(12, 0))
        ttk.Label(controls, textvariable=instruction, style="CardText.TLabel", wraplength=1080).grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 10))
        ttk.Label(controls, text="Printed distance", style="CardTitle.TLabel").grid(row=1, column=0, sticky="w")
        distance_value = tk.StringVar(value=f"{record.known_distance:g}" if record.known_distance else "")
        ttk.Entry(controls, textvariable=distance_value, width=14).grid(row=1, column=1, padx=(6, 12))
        unit_value = tk.StringVar(value=record.known_unit or "µm")
        ttk.Combobox(controls, textvariable=unit_value, values=("µm", "mm"), state="readonly", width=7).grid(row=1, column=2)
        metadata_text = "Unavailable"
        if record.metadata_pixel_width_microns is not None:
            metadata_text = f"{record.metadata_pixel_width_microns:.6g} µm/px"
        ttk.Label(controls, text=f"Current metadata: {metadata_text}", style="CardText.TLabel").grid(row=1, column=3, padx=18, sticky="w")
        proposal_text = tk.StringVar(value="Proposal: enter distance")
        ttk.Label(controls, textvariable=proposal_text, style="CardTitle.TLabel").grid(row=1, column=4, padx=18, sticky="w")

        def calculate() -> float | None:
            if len(points) != 2:
                proposal_text.set("Proposal: select two endpoints")
                return None
            try:
                distance = float(distance_value.get())
            except ValueError:
                proposal_text.set("Proposal: invalid distance")
                return None
            if distance <= 0:
                proposal_text.set("Proposal: distance must be > 0")
                return None
            preview_length = math.dist(points[0], points[1])
            distance_microns = distance * (1000.0 if unit_value.get() == "mm" else 1.0)
            downsample = record.downsample or 1.0
            proposal = distance_microns / (preview_length * downsample)
            proposal_text.set(f"Proposal: {proposal:.6g} µm/px")
            return proposal

        distance_value.trace_add("write", lambda *_args: calculate())
        unit_value.trace_add("write", lambda *_args: calculate())
        calculate()

        buttons = ttk.Frame(review_root, style="App.TFrame")
        buttons.pack(fill="x", pady=(12, 0))

        def confirm() -> None:
            proposal = calculate()
            if proposal is None:
                messagebox.showerror("Calibration review", "Select both endpoints and enter the printed distance.", parent=window)
                return
            if not 0.01 <= proposal <= 100.0:
                if not messagebox.askyesno(
                    "Unusual calibration",
                    f"The proposed value is {proposal:.6g} µm/px, outside the usual 0.01–100 range. Confirm anyway?",
                    parent=window,
                ):
                    return
            metadata = record.metadata_pixel_width_microns
            if metadata and metadata > 0:
                difference = abs(proposal - metadata) / metadata * 100.0
                if difference > 5.0 and not messagebox.askyesno(
                    "Metadata conflict",
                    f"Scale-bar calibration differs from existing metadata by {difference:.1f}%.\n\n"
                    f"Metadata: {metadata:.6g} µm/px\nScale bar: {proposal:.6g} µm/px\n\n"
                    "Confirm the scale-bar result?",
                    parent=window,
                ):
                    return
            record.bar_start_x = round(points[0][0])
            record.bar_start_y = round(points[0][1])
            record.bar_end_x = round(points[1][0])
            record.bar_end_y = round(points[1][1])
            record.bar_preview_length_pixels = math.dist(points[0], points[1])
            record.known_distance = float(distance_value.get())
            record.known_unit = unit_value.get()
            record.proposed_pixel_size_microns = proposal
            record.confirmed = True
            record.applied = False
            record.note = "Human-confirmed scale-bar calibration."
            self._refresh_table()
            window.grab_release()
            window.destroy()
            next_index = next(
                (candidate_index for candidate_index in range(index + 1, len(self.records)) if self.records[candidate_index].preview_path and not self.records[candidate_index].confirmed),
                None,
            )
            if next_index is not None:
                self.table.selection_set(str(next_index))
                self.table.see(str(next_index))
                self.after(80, lambda: self._open_review(next_index))

        ttk.Button(buttons, text="Cancel", command=window.destroy, style="Secondary.TButton").pack(side="right")
        ttk.Button(buttons, text="Confirm & next", command=confirm, style="Accent.TButton").pack(side="right", padx=(0, 8))

    def _manifest_path(self) -> Path:
        return self.output_directory / "calibration_manifest_v1.2.csv"

    def _audit_path(self) -> Path:
        return self.output_directory / "calibration_audit_v1.2.json"

    def _write_calibration_outputs(self) -> Path:
        """Write the compact CSV and full JSON audit together."""
        manifest_path = write_calibration_manifest(self.records, self._manifest_path())
        write_calibration_audit(self.records, self._audit_path())
        return manifest_path

    def _export_manifest(self) -> None:
        if not self.records:
            messagebox.showinfo("Calibration manifest", "No calibration records are available.", parent=self)
            return
        path = self._write_calibration_outputs()
        self._append_log(f"Compact manifest written: {path}")
        self._append_log(f"Full audit written: {self._audit_path()}")
        messagebox.showinfo(
            "Calibration manifest",
            f"Compact CSV:\n{path}\n\nFull JSON audit:\n{self._audit_path()}",
            parent=self,
        )

    def _fiji_command(self, record: CalibrationRecord, output_path: Path, report_path: Path) -> list[str]:
        """Build a blocking Fiji command with explicit paths and calibration."""
        path_values = [record.source_path, str(output_path), str(report_path)]
        if any("'" in value for value in path_values):
            raise ValueError("Fiji calibration paths cannot contain an apostrophe.")
        parameters = (
            f"sourceFile='{Path(record.source_path).as_posix()}',"
            f"outputFile='{output_path.as_posix()}',"
            f"reportFile='{report_path.as_posix()}',"
            f"pixelSizeMicrons='{record.proposed_pixel_size_microns:.12g}'"
        )
        arguments = [
            "--headless",
            "--console",
            "--run",
            str(self.fiji_script),
            parameters,
        ]
        batch_launcher = self.fiji_path.parent / "fiji.bat"
        if batch_launcher.is_file():
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(batch_launcher), *arguments]
        return [str(self.fiji_path), *arguments]

    def _start_fiji_export(self) -> None:
        """Confirm and start sequential calibrated-copy export through Fiji."""
        confirmed = valid_confirmed_fiji_records(self.records)
        if not confirmed:
            messagebox.showerror(
                "Fiji calibration export",
                "No human-confirmed records have an accessible source image and valid pixel size.",
                parent=self,
            )
            return
        if not self.fiji_path.is_file() or not self.fiji_script.is_file():
            messagebox.showerror(
                "Fiji calibration export",
                "Choose an existing Fiji executable in Settings and reopen CALIBRATE.",
                parent=self,
            )
            return
        lines = [
            f"{Path(record.source_path).name}: {record.proposed_pixel_size_microns:.6g} µm/px"
            for record in confirmed
        ]
        if not messagebox.askyesno(
            "Confirm Fiji calibrated copies",
            "Export these human-confirmed calibrations as new OME-TIFF files?\n\n"
            + "\n".join(lines)
            + f"\n\nOutput folder:\n{self.fiji_output_directory}"
            + "\n\nOriginal images and existing calibrated exports will not be overwritten.",
            parent=self,
        ):
            return
        self.fiji_output_directory.mkdir(parents=True, exist_ok=True)
        self.fiji_report_directory.mkdir(parents=True, exist_ok=True)
        self._write_calibration_outputs()
        self._set_running(True)
        self.stop_requested = False
        self.progress_value.set(0)
        self.progress_text.set("Exporting calibrated OME-TIFF files through Fiji")
        threading.Thread(target=self._fiji_worker, args=(confirmed,), daemon=True).start()

    def _fiji_worker(self, records: list[CalibrationRecord]) -> None:
        """Export and verify confirmed records one at a time to bound memory use."""
        failures: list[str] = []
        for index, record in enumerate(records):
            if self.stop_requested:
                break
            output_path = next_fiji_output_path(
                self.fiji_output_directory,
                Path(record.source_path),
            )
            report_path = self.fiji_report_directory / f"{output_path.stem}_verification.json"
            self.events.put(("log", f"[{index + 1}/{len(records)}] Fiji export: {Path(record.source_path).name}"))
            try:
                command = self._fiji_command(record, output_path, report_path)
                self.process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                assert self.process.stdout is not None
                tail = [line.rstrip() for line in self.process.stdout if line.strip()][-12:]
                return_code = self.process.wait()
                if return_code != 0 or not output_path.is_file() or not report_path.is_file():
                    raise RuntimeError("\n".join(tail) or f"Fiji exited with code {return_code} without a verification report.")
                report = json.loads(report_path.read_text(encoding="utf-8"))
                if not bool(report.get("verified")):
                    raise RuntimeError("Fiji verification did not pass.")
                expected = float(record.proposed_pixel_size_microns)
                width = float(report.get("pixelWidthMicrons"))
                height = float(report.get("pixelHeightMicrons"))
                tolerance = max(1.0e-9, expected * 1.0e-6)
                if abs(width - expected) > tolerance or abs(height - expected) > tolerance:
                    raise RuntimeError("The verification report contains a different pixel size.")
                record.fiji_output_path = str(output_path)
                record.fiji_verified_pixel_width_microns = width
                record.fiji_verified_pixel_height_microns = height
                record.fiji_verified = True
                record.note = "Human-confirmed calibration exported and verified by Fiji."
                self.events.put(("log", f"[{index + 1}/{len(records)}] VERIFIED: {output_path.name}"))
            except Exception as exc:
                failures.append(f"{Path(record.source_path).name}: {exc}")
                record.fiji_verified = False
                record.note = f"Fiji export failed: {exc}"
                self.events.put(("log", f"[{index + 1}/{len(records)}] FAILED: {exc}"))
            finally:
                self.process = None
            self.events.put(("record", self.records.index(record)))
            self.events.put(("progress", ((index + 1) / len(records) * 100.0, f"Fiji export {index + 1}/{len(records)}")))
        self._write_calibration_outputs()
        self.events.put(("fiji_done", failures))

    def _start_apply(self) -> None:
        confirmed = valid_confirmed_records(self.records)
        if not confirmed:
            messagebox.showerror(
                "Apply calibration",
                "No human-confirmed records are matched to entries in the selected QuPath project.",
                parent=self,
            )
            return
        lines = [
            f"{record.image_name}: {record.proposed_pixel_size_microns:.6g} µm/px"
            for record in confirmed
        ]
        if not messagebox.askyesno(
            "Confirm QuPath calibration write",
            "Apply these human-confirmed calibrations to QuPath metadata?\n\n"
            + "\n".join(lines)
            + "\n\nThe source images will not be modified. A project and data.qpdata backup will be created first.",
            parent=self,
        ):
            return
        try:
            backups = backup_project_calibration_targets(self.project_path, confirmed)
            self._write_calibration_outputs()
        except OSError as exc:
            messagebox.showerror("Apply calibration", f"Could not create backups:\n{exc}", parent=self)
            return
        self._append_log(f"Created {len(backups)} backup file(s) before calibration write.")
        self._set_running(True)
        self.stop_requested = False
        self.progress_value.set(0)
        self.progress_text.set("Applying confirmed calibration sequentially")
        threading.Thread(target=self._apply_worker, args=(confirmed,), daemon=True).start()

    def _apply_worker(self, records: list[CalibrationRecord]) -> None:
        failures: list[str] = []
        for index, record in enumerate(records):
            if self.stop_requested:
                break
            command = [
                str(self.qupath_path),
                "-q",
                "script",
                "-p",
                str(self.project_path),
                "-i",
                record.image_name,
                "--save",
                str(self.apply_script),
                "--args",
                f"pixelSizeMicrons={record.proposed_pixel_size_microns:.12g}",
            ]
            applied = False
            tail: list[str] = []
            try:
                self.process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                assert self.process.stdout is not None
                for raw_line in self.process.stdout:
                    line = raw_line.rstrip()
                    if line:
                        tail.append(line)
                        tail = tail[-10:]
                    event = self._decode_event(line)
                    if event and event.get("kind") == "calibration_applied":
                        applied = True
                return_code = self.process.wait()
                if return_code != 0 or not applied:
                    raise RuntimeError("\n".join(tail[-5:]) or f"QuPath exited with code {return_code}.")
                record.applied = True
                self.events.put(("log", f"[{index + 1}/{len(records)}] Applied: {record.image_name}"))
            except Exception as exc:
                failures.append(f"{record.image_name}: {exc}")
                self.events.put(("log", f"[{index + 1}/{len(records)}] FAILED: {record.image_name}: {exc}"))
            finally:
                self.process = None
            self.events.put(("record", self.records.index(record)))
            self.events.put(("progress", ((index + 1) / len(records) * 100.0, f"Applied {index + 1}/{len(records)}")))
        self._write_calibration_outputs()
        self.events.put(("apply_done", failures))

    def _stop(self) -> None:
        self.stop_requested = True
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self._append_log("Stop requested. No additional image will start.")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "record":
                    self._refresh_table()
                elif kind == "progress":
                    percent, text = payload
                    self.progress_value.set(percent)
                    self.progress_text.set(text)
                elif kind == "scan_done":
                    self._set_running(False)
                    scanned = sum(bool(record.preview_path) for record in self.records)
                    self.progress_text.set(f"Scan complete — {scanned}/{len(self.records)} previews")
                    self._append_log("Scan complete. Human review is required before any calibration can be applied.")
                    first = next((i for i, record in enumerate(self.records) if record.preview_path and not record.confirmed), None)
                    if first is not None:
                        self.table.selection_set(str(first))
                        self.table.see(str(first))
                        self.after(100, lambda: self._open_review(first))
                elif kind == "apply_done":
                    self._set_running(False)
                    failures = list(payload or [])
                    if failures:
                        self.progress_text.set("Calibration apply finished with errors")
                        messagebox.showwarning(
                            "Calibration apply",
                            f"Completed with {len(failures)} failure(s). See the calibration log and manifest.",
                            parent=self,
                        )
                    else:
                        self.progress_value.set(100)
                        self.progress_text.set("Confirmed calibrations applied")
                        messagebox.showinfo(
                            "Calibration apply",
                            "All confirmed calibrations were applied to QuPath metadata. Source images were not modified.",
                            parent=self,
                        )
                elif kind == "fiji_done":
                    self._set_running(False)
                    failures = list(payload or [])
                    verified = sum(record.fiji_verified for record in self.records)
                    if failures:
                        self.progress_text.set("Fiji export finished with errors")
                        messagebox.showwarning(
                            "Fiji calibration export",
                            f"Verified {verified} calibrated file(s); {len(failures)} failed. See the log and manifest.",
                            parent=self,
                        )
                    else:
                        self.progress_value.set(100)
                        self.progress_text.set("Fiji calibrated copies verified")
                        messagebox.showinfo(
                            "Fiji calibration export",
                            f"Verified {verified} calibrated OME-TIFF file(s).\n\nSaved under:\n{self.fiji_output_directory}",
                            parent=self,
                        )
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain_events)

    def _close(self) -> None:
        if self.running:
            messagebox.showwarning("Calibration", "Stop the active calibration task before closing this window.", parent=self)
            return
        self.destroy()
