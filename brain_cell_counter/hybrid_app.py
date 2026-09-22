"""Build the desktop UI and coordinate sequential QuPath/Cellpose processes.

Scientific algorithms remain in the QuPath engine and threshold module. This
module owns user input, safety confirmations, logging, cancellation, and
versioned checkpoint orchestration.
"""

from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from urllib.parse import unquote, urlparse

from .threshold_optimizer import calculate_global_thresholds, write_threshold_reports
from .calibration_wizard import CalibrationWizard
from .results_preview import ResultsPreviewTab
from .version import APP_VERSION, APP_VERSION_LABEL, OUTPUT_SCHEMA_VERSION


USER_HOME = Path.home()
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", USER_HOME / "AppData" / "Local"))
DEFAULT_QUPATH = LOCAL_APP_DATA / "QuPath-0.7.0" / "QuPath-0.7.0 (console).exe"
DEFAULT_PROJECT = USER_HOME / "Documents" / "QuPath" / "project.qpproj"
DEFAULT_CELLPOSE_PYTHON = USER_HOME / "miniforge3" / "envs" / "cellpose" / "python.exe"
FIJI_CANDIDATES = (
    USER_HOME / "Downloads" / "fiji-latest-win64-jdk" / "Fiji" / "fiji-windows-x64.exe",
    USER_HOME / "Fiji.app" / "ImageJ-win64.exe",
    LOCAL_APP_DATA / "Fiji" / "fiji-windows-x64.exe",
)
DEFAULT_FIJI = next((path for path in FIJI_CANDIDATES if path.is_file()), FIJI_CANDIDATES[0])
SETTINGS_FILE = Path(__file__).resolve().parent.parent / "brain_cell_counter_settings.json"
INDIVIDUAL_THRESHOLD_TYPE = "Individual channels"
CHANNEL_12_THRESHOLD_TYPE = "Channel 1 positive + Channel 2 positive"
CHANNEL_13_THRESHOLD_TYPE = "Channel 1 positive + Channel 3 positive"
CHANNEL_23_THRESHOLD_TYPE = "Channel 2 positive + Channel 3 positive"
THRESHOLD_PAIR_BY_TYPE = {
    CHANNEL_12_THRESHOLD_TYPE: ("channel1", "channel2"),
    CHANNEL_13_THRESHOLD_TYPE: ("channel1", "channel3"),
    CHANNEL_23_THRESHOLD_TYPE: ("channel2", "channel3"),
}
WINDOW_SIZE_RECOMMENDED = "Recommended (automatic monitor fit)"
WINDOW_SIZE_COMPACT = "Compact (1080 x 820)"
WINDOW_SIZE_LARGE = "Large (1440 x 960)"
WINDOW_SIZE_MAXIMIZED = "Maximized"
WINDOW_SIZE_PRESETS = (
    WINDOW_SIZE_RECOMMENDED,
    WINDOW_SIZE_COMPACT,
    WINDOW_SIZE_LARGE,
    WINDOW_SIZE_MAXIMIZED,
)


def qupath_gui_executable(configured_path: Path) -> Path:
    """Prefer QuPath's windowed launcher when settings point to its console launcher."""
    console_suffix = " (console).exe"
    if configured_path.name.lower().endswith(console_suffix):
        gui_name = configured_path.name[: -len(console_suffix)] + ".exe"
        gui_path = configured_path.with_name(gui_name)
        if gui_path.is_file():
            return gui_path
    return configured_path


class ToolTip:
    """Display delayed, non-modal English help for a single UI control."""
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        self.after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self.after_id = self.widget.after(350, self._show)

    def _cancel(self) -> None:
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def _show(self) -> None:
        self.after_id = None
        if self.window is not None:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{self.widget.winfo_rootx() + 18}+{self.widget.winfo_rooty() + 22}")
        label = tk.Label(
            self.window,
            text=self.text,
            justify="left",
            wraplength=420,
            background="#fffbd6",
            foreground="#202020",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            font=("Segoe UI", 9),
        )
        label.pack()

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None


class HybridApp(tk.Tk):
    """Main application window for the guided brain-cell-counting workflow."""
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Brain Cell Counter {APP_VERSION_LABEL} - QuPath Hybrid")
        self.geometry("1080x820")
        self.minsize(980, 740)
        self.events: queue.Queue = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.worker: threading.Thread | None = None
        self.project_entries: dict[str, int] = {}
        self.last_output_folder: Path | None = None
        self.progress_value = tk.DoubleVar(value=0.0)
        self.progress_text = tk.StringVar(value="Ready — 0%")
        self.run_state_text = tk.StringVar(value="READY")
        self.expected_cellpose_tiles = 0
        self.read_cellpose_tiles = 0
        self.batch_cancel_requested = False
        self.is_running = False
        self.parameter_entries: dict[str, ttk.Entry] = {}
        self.threshold_profiles: list[dict] = []
        self.log_window: tk.Toplevel | None = None
        self.log_text: tk.Text | None = None
        self.advanced_cellpose_window: tk.Toplevel | None = None
        self.advanced_threshold_window: tk.Toplevel | None = None

        self.values = {
            "qupath": tk.StringVar(value=str(DEFAULT_QUPATH)),
            "project": tk.StringVar(value=str(DEFAULT_PROJECT) if DEFAULT_PROJECT.is_file() else ""),
            "image": tk.StringVar(),
            "cellpose_python": tk.StringVar(value=str(DEFAULT_CELLPOSE_PYTHON)),
            "fiji": tk.StringVar(value=str(DEFAULT_FIJI)),
            "neun_channel": tk.StringVar(value="1"),
            "cfos_channel": tk.StringVar(value="2"),
            "dapi_channel": tk.StringVar(value="3"),
            "neun_threshold": tk.StringVar(value="50"),
            "cfos_threshold": tk.StringVar(value="50"),
            "dapi_threshold": tk.StringVar(value="50"),
            "require_dapi": tk.BooleanVar(value=False),
            "cellprob": tk.StringVar(value="-0.5"),
            "flow": tk.StringVar(value="0.4"),
            "reuse": tk.BooleanVar(value=True),
            "channel_1_name": tk.StringVar(value="NeuN"),
            "channel_2_name": tk.StringVar(value="c-Fos"),
            "channel_3_name": tk.StringVar(value="DAPI"),
            "hemisphere_mode": tk.StringVar(value="Left and Right"),
            "threshold_scope": tk.StringVar(value="Current project — all entries"),
            "threshold_project": tk.StringVar(value=""),
            "channel1_search_start": tk.StringVar(value="0"),
            "channel1_search_end": tk.StringVar(value="200"),
            "channel1_search_step": tk.StringVar(value="5"),
            "channel2_search_start": tk.StringVar(value="0"),
            "channel2_search_end": tk.StringVar(value="200"),
            "channel2_search_step": tk.StringVar(value="5"),
            "channel3_search_start": tk.StringVar(value="0"),
            "channel3_search_end": tk.StringVar(value="200"),
            "channel3_search_step": tk.StringVar(value="5"),
            "total_change_limit": tk.StringVar(value="3"),
            "section_change_limit": tk.StringVar(value="5"),
            "stable_section_percent": tk.StringVar(value="90"),
            "minimum_stable_steps": tk.StringVar(value="3"),
            "threshold_objective": tk.StringVar(value=CHANNEL_12_THRESHOLD_TYPE),
            "automatic_background_guard": tk.BooleanVar(value=True),
            "threshold_locked": tk.BooleanVar(value=False),
            "ui_font_size": tk.StringVar(value="9"),
            "log_font_size": tk.StringVar(value="9"),
            "window_width": tk.StringVar(value="1080"),
            "window_height": tk.StringVar(value="820"),
            "window_size_preset": tk.StringVar(value=WINDOW_SIZE_RECOMMENDED),
        }
        self._load_saved_channel_names()
        self._apply_ui_preferences(show_error=False)
        self._build_ui()
        self._update_threshold_lock_state()
        if Path(self.values["project"].get().strip()).is_file():
            self._load_project()
        self.after(100, self._drain_events)

    @staticmethod
    def _help_label(parent: tk.Widget, text: str, help_text: str) -> ttk.Frame:
        frame = ttk.Frame(parent)
        ttk.Label(frame, text=text).pack(side="left")
        question = tk.Label(
            frame,
            text="?",
            width=2,
            foreground="#10A37F",
            cursor="question_arrow",
            font=("Segoe UI", 9, "bold"),
        )
        question.pack(side="left", padx=(2, 0))
        ToolTip(question, help_text)
        return frame

    def _show_advanced_cellpose_window(self) -> None:
        """Show Cellpose tuning fields in a visible, dedicated window."""
        if self.advanced_cellpose_window is not None and self.advanced_cellpose_window.winfo_exists():
            self.advanced_cellpose_window.deiconify()
            self.advanced_cellpose_window.lift()
            self.advanced_cellpose_window.focus_force()
            return
        window = tk.Toplevel(self)
        self.advanced_cellpose_window = window
        window.title("Advanced Cellpose settings")
        window.geometry("620x310")
        window.minsize(540, 280)
        window.transient(self)

        def close_window() -> None:
            self.advanced_cellpose_window = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close_window)

        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Advanced Cellpose segmentation", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            frame,
            text="These values are used directly by VALIDATE, AUTO SUGGEST CELLPOSE, and ANALYZE.",
            style="Subtitle.TLabel",
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 14))
        frame.columnconfigure(1, weight=1)
        self._help_label(
            frame,
            "Cellprob threshold",
            "Lower values usually create more masks; higher values are more selective.",
        ).grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
        ttk.Entry(frame, textvariable=self.values["cellprob"], width=18).grid(
            row=2, column=1, sticky="ew", pady=6
        )
        self._help_label(
            frame,
            "Flow threshold",
            "Lower values reject more questionable masks; higher values accept more masks.",
        ).grid(row=3, column=0, sticky="w", padx=(0, 12), pady=6)
        ttk.Entry(frame, textvariable=self.values["flow"], width=18).grid(
            row=3, column=1, sticky="ew", pady=6
        )
        ttk.Label(
            frame,
            text="Changing these settings can alter segmentation and cell counts. Review masks in QuPath before a full batch.",
            foreground="#9a5700",
            wraplength=560,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(14, 8))
        ttk.Button(frame, text="Done", command=close_window).grid(
            row=5, column=0, columnspan=2, sticky="e", pady=(8, 0)
        )

    def _show_advanced_threshold_window(self) -> None:
        """Show detailed plateau ranges and stability criteria in a dialog."""
        if self.advanced_threshold_window is not None and self.advanced_threshold_window.winfo_exists():
            self.advanced_threshold_window.deiconify()
            self.advanced_threshold_window.lift()
            self.advanced_threshold_window.focus_force()
            return
        window = tk.Toplevel(self)
        self.advanced_threshold_window = window
        window.title("Advanced plateau settings")
        window.geometry("780x470")
        window.minsize(700, 430)
        window.transient(self)

        def close_window() -> None:
            self.advanced_threshold_window = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close_window)

        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Advanced plateau settings", style="Section.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w"
        )
        ttk.Label(
            frame,
            text="The recommended defaults work for the first scan. Change these only when a wider or finer search is required.",
            style="Subtitle.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(3, 14))
        for column in (1, 3, 5):
            frame.columnconfigure(column, weight=1)

        threshold_help = {
            "channel1_search_start": "The smallest Channel 1 threshold to test.",
            "channel1_search_end": "The largest Channel 1 threshold to test.",
            "channel1_search_step": "The interval between Channel 1 thresholds.",
            "channel2_search_start": "The smallest Channel 2 threshold to test.",
            "channel2_search_end": "The largest Channel 2 threshold to test.",
            "channel2_search_step": "The interval between Channel 2 thresholds.",
            "channel3_search_start": "The smallest Channel 3 threshold to test.",
            "channel3_search_end": "The largest Channel 3 threshold to test.",
            "channel3_search_step": "The interval between Channel 3 thresholds.",
            "total_change_limit": "Maximum stable change in the total positive-object count.",
            "section_change_limit": "Maximum stable change for each individual section.",
            "stable_section_percent": "Minimum percentage of sections that must be stable.",
            "minimum_stable_steps": "Minimum consecutive stable steps required for a plateau.",
        }
        fields = (
            ("Ch1 start", "channel1_search_start", 2, 0),
            ("End", "channel1_search_end", 2, 2),
            ("Step", "channel1_search_step", 2, 4),
            ("Ch2 start", "channel2_search_start", 3, 0),
            ("End", "channel2_search_end", 3, 2),
            ("Step", "channel2_search_step", 3, 4),
            ("Ch3 start", "channel3_search_start", 4, 0),
            ("End", "channel3_search_end", 4, 2),
            ("Step", "channel3_search_step", 4, 4),
            ("Total change < %", "total_change_limit", 6, 0),
            ("Section change < %", "section_change_limit", 6, 2),
            ("Stable sections ≥ %", "stable_section_percent", 6, 4),
            ("Consecutive steps ≥", "minimum_stable_steps", 7, 0),
        )
        ttk.Separator(frame).grid(row=5, column=0, columnspan=6, sticky="ew", pady=12)
        for label, key, row, column in fields:
            self._help_label(frame, label, threshold_help[key]).grid(
                row=row, column=column, sticky="w", padx=(0, 5), pady=5
            )
            ttk.Entry(frame, textvariable=self.values[key], width=10).grid(
                row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=5
            )
        ttk.Button(frame, text="Done", command=close_window).grid(
            row=8, column=4, columnspan=2, sticky="e", pady=(18, 0)
        )

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        colors = {
            "background": "#FFFFFF",
            "surface": "#F7F7F8",
            "text": "#212121",
            "muted": "#6B6B6B",
            "border": "#E5E5E5",
            "primary": "#212121",
            "primary_active": "#000000",
            "accent": "#10A37F",
            "success": "#0F8A6A",
            "warning": "#A16207",
            "danger": "#D00E17",
        }
        self.configure(background=colors["background"])
        style.configure("TFrame", background=colors["background"])
        style.configure("TLabel", background=colors["background"], foreground=colors["text"])
        style.configure("TCheckbutton", background=colors["background"], foreground=colors["text"])
        style.map("TCheckbutton", background=[("active", colors["background"])])
        style.configure(
            "TLabelframe",
            background=colors["background"],
            bordercolor=colors["border"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=colors["background"],
            foreground=colors["text"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure("Header.TFrame", background=colors["surface"])
        style.configure(
            "Title.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=("Segoe UI", 17, "bold"),
        )
        style.configure("HeaderSubtitle.TLabel", background=colors["surface"], foreground=colors["muted"])
        style.configure("Subtitle.TLabel", foreground=colors["muted"])
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"), foreground=colors["text"])
        style.configure("Step.TLabel", font=("Segoe UI", 10, "bold"), foreground=colors["accent"])
        style.configure("Surface.TFrame", background=colors["surface"], relief="solid", borderwidth=1)
        style.configure("Surface.TLabel", background=colors["surface"], foreground=colors["text"])
        style.configure(
            "Context.TLabel",
            background=colors["surface"],
            foreground=colors["text"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure("Muted.TLabel", background=colors["surface"], foreground=colors["muted"])
        style.configure("Card.TFrame", background=colors["surface"], relief="solid", borderwidth=1)
        style.configure(
            "CardStep.TLabel",
            background=colors["surface"],
            foreground=colors["accent"],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure("CardBody.TLabel", background=colors["surface"], foreground=colors["text"])
        style.configure(
            "Accent.TButton",
            background=colors["primary"],
            foreground="#FFFFFF",
            font=("Segoe UI", 10, "bold"),
            padding=(16, 8),
            borderwidth=0,
        )
        style.map(
            "Accent.TButton",
            background=[("pressed", colors["primary_active"]), ("active", "#2F2F2F"), ("disabled", "#B4B4B4")],
            foreground=[("disabled", "#EDF2F7")],
        )
        style.configure("Action.TButton", background=colors["surface"], foreground=colors["text"], padding=(12, 6))
        style.map("Action.TButton", background=[("active", "#ECECEC")])
        style.configure("Danger.TButton", background="#FDECEC", foreground=colors["danger"], padding=(12, 6))
        style.map("Danger.TButton", background=[("active", "#FAD5D5")])
        style.configure(
            "Header.TButton",
            background=colors["surface"],
            foreground=colors["text"],
            padding=(12, 6),
            borderwidth=1,
        )
        style.map("Header.TButton", background=[("active", "#ECECEC")])
        style.configure("TNotebook", background=colors["background"], borderwidth=0)
        style.configure("TNotebook.Tab", background="#F7F7F8", foreground="#6B6B6B", padding=(18, 9), font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#ECECEC"), ("active", "#F0F0F0")], foreground=[("selected", colors["text"])])
        style.configure("Horizontal.TProgressbar", background=colors["accent"], troughcolor="#ECECEC", borderwidth=0)
        style.configure("StatusReady.TLabel", background="#E8F7F2", foreground=colors["success"], padding=(9, 4), font=("Segoe UI", 8, "bold"))
        style.configure("StatusRunning.TLabel", background="#ECECEC", foreground=colors["text"], padding=(9, 4), font=("Segoe UI", 8, "bold"))
        style.configure("StatusComplete.TLabel", background="#EAF7EE", foreground=colors["success"], padding=(9, 4), font=("Segoe UI", 8, "bold"))
        style.configure("StatusError.TLabel", background="#FDECEC", foreground=colors["danger"], padding=(9, 4), font=("Segoe UI", 8, "bold"))
        style.configure("StatusCancelled.TLabel", background="#FFF4E5", foreground=colors["warning"], padding=(9, 4), font=("Segoe UI", 8, "bold"))
        style.configure("Warning.TLabel", background="#FFF7ED", foreground=colors["warning"], padding=(10, 6))

        root = ttk.Frame(self, padding=(12, 12, 12, 10))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root, padding=(14, 8), style="Header.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=f"Brain Cell Counter {APP_VERSION_LABEL}", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(header, text="Settings", command=self._show_settings_window, style="Header.TButton").grid(
            row=0, column=1, rowspan=2, padx=(8, 0)
        )
        ttk.Button(header, text="Help", command=self._show_help_window, style="Header.TButton").grid(
            row=0, column=2, rowspan=2, padx=(8, 0)
        )
        ttk.Label(
            header,
            text="A guided workflow from ABBA/DeepSlice registration to reproducible Allen regional counts",
            style="HeaderSubtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        context_bar = ttk.Frame(root, padding=(10, 5), style="Surface.TFrame")
        context_bar.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        context_bar.columnconfigure(1, weight=1)
        ttk.Label(context_bar, text="CURRENT PROJECT", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.project_context = ttk.Label(
            context_bar,
            text="No project selected",
            style="Context.TLabel",
            anchor="w",
        )
        self.project_context.grid(row=0, column=1, sticky="ew", padx=(10, 8))
        ttk.Button(context_bar, text="Change…", command=self._browse_project, style="Action.TButton").grid(row=0, column=2)

        notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky="nsew")
        self.notebook = notebook
        workflow_tab = ttk.Frame(notebook, padding=12)
        analysis_tab = ttk.Frame(notebook, padding=12)
        threshold_tab = ttk.Frame(notebook, padding=12)
        results_tab = ttk.Frame(notebook, padding=12)
        for tab in (workflow_tab, analysis_tab, threshold_tab, results_tab):
            tab.columnconfigure(0, weight=1)
        notebook.add(workflow_tab, text="1  START")
        notebook.add(analysis_tab, text="2  ANALYSIS")
        notebook.add(threshold_tab, text="3  THRESHOLDS")
        notebook.add(results_tab, text="4  RESULTS")

        results_tab.rowconfigure(0, weight=1)
        self.results_preview = ResultsPreviewTab(
            results_tab,
            self._open_results,
            self._open_result_in_qupath,
        )
        self.results_preview.grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            workflow_tab,
            text="Choose one QuPath project, prepare its image entries, then validate before analysis.",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        project_frame = ttk.LabelFrame(workflow_tab, text="Step 1 — Choose project", padding=12)
        project_frame.grid(row=1, column=0, sticky="ew")
        project_frame.columnconfigure(1, weight=1)
        self._help_label(
            project_frame, "Project (.qpproj)", "The project.qpproj file containing image entries, detections, and atlas data created by ABBA."
        ).grid(row=0, column=0, sticky="w")
        ttk.Entry(project_frame, textvariable=self.values["project"]).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Button(project_frame, text="Browse…", command=self._browse_project).grid(row=0, column=2)
        self._help_label(
            project_frame, "Image entry", "The selected image used by VALIDATE, ANALYZE, or automatic Cellpose parameter suggestion."
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.image_combo = ttk.Combobox(project_frame, textvariable=self.values["image"], state="readonly")
        self.image_combo.grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        self.image_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_local_status())
        ttk.Button(project_frame, text="Refresh", command=self._load_project).grid(row=1, column=2, pady=(8, 0))

        self.local_status = ttk.Label(project_frame, text="Choose a project.", foreground="#555555")
        self.local_status.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))

        preparation = ttk.LabelFrame(workflow_tab, text="Step 2 — Prepare project", padding=12)
        preparation.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for column in range(3):
            preparation.columnconfigure(column, weight=1, uniform="prepare")

        calibration_card = ttk.Frame(preparation, padding=(12, 10), style="Card.TFrame")
        calibration_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(calibration_card, text="A. Calibration", style="CardStep.TLabel").pack(anchor="w")
        ttk.Label(
            calibration_card,
            text="Recover pixel size from scale bars when image metadata is missing or incorrect.",
            wraplength=290,
            justify="left",
            style="CardBody.TLabel",
        ).pack(anchor="w", fill="x", pady=(3, 8))
        self.calibration_button = ttk.Button(
            calibration_card,
            text="CALIBRATE IMAGES",
            command=self._show_calibration_wizard,
            style="Action.TButton",
        )
        self.calibration_button.pack(fill="x")

        atlas_card = ttk.Frame(preparation, padding=(12, 10), style="Card.TFrame")
        atlas_card.grid(row=0, column=1, sticky="nsew", padx=8)
        ttk.Label(atlas_card, text="B. Atlas annotations", style="CardStep.TLabel").pack(anchor="w")
        ttk.Label(
            atlas_card,
            text="Import each entry's existing ABBA/DeepSlice registration into QuPath.",
            wraplength=290,
            justify="left",
            style="CardBody.TLabel",
        ).pack(anchor="w", fill="x", pady=(3, 8))
        self.import_abba_button = ttk.Button(
            atlas_card,
            text="IMPORT ABBA",
            command=self._start_abba_import_batch,
            style="Action.TButton",
        )
        self.import_abba_button.pack(fill="x")
        ToolTip(
            self.import_abba_button,
            "Imports and saves atlas annotations for every project entry that already contains an ABBA ROI set and transform. Entries without an exported ABBA/DeepSlice registration are reported and skipped.",
        )
        ToolTip(
            self.calibration_button,
            "Adds many stitched images, scans scale bars sequentially, requires human confirmation for every proposed calibration, and then writes only confirmed values to backed-up QuPath metadata. Source images are never resized or overwritten.",
        )

        preflight_card = ttk.Frame(preparation, padding=(12, 10), style="Card.TFrame")
        preflight_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        ttk.Label(preflight_card, text="C. File links", style="CardStep.TLabel").pack(anchor="w")
        ttk.Label(
            preflight_card,
            text="Check that every project entry can still find its original source image.",
            wraplength=290,
            justify="left",
            style="CardBody.TLabel",
        ).pack(anchor="w", fill="x", pady=(3, 8))
        ttk.Button(
            preflight_card,
            text="RUN PREFLIGHT",
            command=self._run_project_preflight,
            style="Action.TButton",
        ).pack(fill="x")

        next_step = ttk.LabelFrame(workflow_tab, text="Step 3 — Validate and analyze", padding=12)
        next_step.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            next_step,
            text="Close the QuPath desktop window, review Analysis, click VALIDATE, then choose ANALYZE. Project entries are processed sequentially from top to bottom.",
            wraplength=940,
            justify="left",
        ).pack(anchor="w")

        ttk.Label(analysis_tab, text="Cell analysis setup", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            analysis_tab,
            text="These settings are shared by validation and every selected entry in the analysis batch.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 10))
        channels = ttk.LabelFrame(analysis_tab, text="1 — Name the image channels", padding=12)
        channels.grid(row=2, column=0, sticky="ew")
        for column in range(3):
            channels.columnconfigure(column, weight=1, uniform="channels")
        parameter_help = {
            "channel_1_name": "Optional display name for Channel 1. This name does not change the global-threshold calculation.",
            "channel_2_name": "Optional display name for Channel 2. This name does not change the global-threshold calculation.",
            "channel_3_name": "Optional display name for Channel 3. This name does not change the global-threshold calculation.",
            "neun_threshold": "Mean-intensity cutoff for Channel 1. A detection is positive when its mean value is greater than or equal to this number.",
            "cfos_threshold": "Mean-intensity cutoff for Channel 2. A detection is positive when its mean value is greater than or equal to this number.",
            "dapi_threshold": "Mean-intensity cutoff for Channel 3. It is applied only when the Channel 3 threshold requirement is enabled.",
            "cellprob": "Cellpose object-probability threshold. Lower values usually create more masks; higher values are more selective.",
            "flow": "Maximum Cellpose flow-consistency error. Lower values reject more questionable masks; higher values accept more masks.",
        }
        channel_fields = (
            ("Channel 1 (RED)", "channel_1_name"),
            ("Channel 2 (GREEN)", "channel_2_name"),
            ("Channel 3 (BLUE)", "channel_3_name"),
        )
        for column, (label, key) in enumerate(channel_fields):
            field = ttk.Frame(channels)
            field.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
            self._help_label(field, label, parameter_help[key]).pack(anchor="w")
            entry = ttk.Entry(field, textvariable=self.values[key])
            entry.pack(fill="x", pady=(3, 0))
            self.parameter_entries[key] = entry
        ttk.Button(channels, text="Save channel names", command=self._save_channel_names).grid(
            row=1, column=0, columnspan=3, sticky="e", pady=(8, 0)
        )

        thresholds = ttk.LabelFrame(analysis_tab, text="2 — Set positive-intensity thresholds", padding=12)
        thresholds.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        for column in range(3):
            thresholds.columnconfigure(column, weight=1, uniform="thresholds")
        threshold_fields = (
            ("Channel 1 threshold", "neun_threshold"),
            ("Channel 2 threshold", "cfos_threshold"),
            ("Channel 3 threshold", "dapi_threshold"),
        )
        for column, (label, key) in enumerate(threshold_fields):
            field = ttk.Frame(thresholds)
            field.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
            self._help_label(field, label, parameter_help[key]).pack(anchor="w")
            entry = ttk.Entry(field, textvariable=self.values[key])
            entry.pack(fill="x", pady=(3, 0))
            self.parameter_entries[key] = entry
        channel3_requirement = ttk.Frame(thresholds)
        channel3_requirement.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            channel3_requirement,
            text="Require Channel 3 intensity threshold",
            variable=self.values["require_dapi"],
        ).pack(side="left")
        channel3_help = tk.Label(
            channel3_requirement, text="?", width=2, foreground="#10A37F",
            cursor="question_arrow", font=("Segoe UI", 9, "bold")
        )
        channel3_help.pack(side="left")
        ToolTip(channel3_help, "When enabled, a detection must also pass the Channel 3 intensity threshold to be counted as valid.")

        behavior = ttk.LabelFrame(analysis_tab, text="3 — Choose analysis behavior", padding=12)
        behavior.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        behavior.columnconfigure(1, weight=1)
        behavior.columnconfigure(3, weight=1)
        reuse_frame = ttk.Frame(behavior)
        reuse_frame.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            reuse_frame,
            text="Reuse existing detections",
            variable=self.values["reuse"],
        ).pack(side="left")
        reuse_help = tk.Label(
            reuse_frame, text="?", width=2, foreground="#10A37F",
            cursor="question_arrow", font=("Segoe UI", 9, "bold")
        )
        reuse_help.pack(side="left")
        ToolTip(
            reuse_help,
            "Enabled: reuse existing Cellpose detections and only reclassify/recount them. Disabled: rerun Cellpose, which takes longer but creates new segmentation.",
        )
        hemisphere_frame = ttk.Frame(behavior)
        hemisphere_frame.grid(row=0, column=2, columnspan=2, sticky="ew", padx=(20, 0))
        ttk.Label(hemisphere_frame, text="Hemisphere").pack(side="left", padx=(0, 2))
        hemisphere_help = tk.Label(
            hemisphere_frame, text="?", width=2, foreground="#10A37F",
            cursor="question_arrow", font=("Segoe UI", 9, "bold")
        )
        hemisphere_help.pack(side="left", padx=(0, 4))
        ToolTip(
            hemisphere_help,
            "Left and Right exports the two hemispheres separately. Do not split combines both sides into one result for each Allen region.",
        )
        ttk.Combobox(
            hemisphere_frame,
            textvariable=self.values["hemisphere_mode"],
            values=("Left and Right", "Do not split"),
            state="readonly",
            width=16,
        ).pack(side="left", fill="x", expand=True)

        advanced_actions = ttk.Frame(analysis_tab)
        advanced_actions.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.calibrate_button = ttk.Button(
            advanced_actions,
            text="AUTO SUGGEST CELLPOSE",
            command=lambda: self._start("cellpose-calibrate"),
        )
        self.calibrate_button.pack(side="left")
        self.cellpose_advanced_button = ttk.Button(
            advanced_actions,
            text="Advanced Cellpose settings…",
            command=self._show_advanced_cellpose_window,
        )
        self.cellpose_advanced_button.pack(side="left", padx=(8, 0))
        ToolTip(
            self.calibrate_button,
            "Tests 15 Cellprob/Flow pairs on representative crops without saving project changes.",
        )

        ttk.Label(threshold_tab, text="Dataset-wide threshold finder", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            threshold_tab,
            text="Advanced: identify a stable plateau across a project before locking one threshold per channel for the batch.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 10))
        threshold_finder = ttk.LabelFrame(threshold_tab, text="Threshold search", padding=12)
        threshold_finder.grid(row=2, column=0, sticky="ew")
        threshold_finder.columnconfigure(1, weight=1)
        threshold_finder.columnconfigure(3, weight=1)

        self._help_label(
            threshold_finder, "Sections", "Choose the data used to find the plateau: the entire current project, one test image, or another project."
        ).grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Combobox(
            threshold_finder,
            textvariable=self.values["threshold_scope"],
            values=(
                "Current project — all entries",
                "Selected entry only — test",
                "Chosen project file — all entries",
            ),
            state="readonly",
            width=29,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 18))
        self._help_label(
            threshold_finder,
            "Threshold type",
            "Choose Individual channels to optimize all three channels separately, or choose a two-channel positive pair. Pair mode maximizes double-positive detections inside both valid stable plateaus.",
        ).grid(row=0, column=2, sticky="w", padx=(0, 5))
        ttk.Combobox(
            threshold_finder,
            textvariable=self.values["threshold_objective"],
            values=(
                CHANNEL_12_THRESHOLD_TYPE,
                CHANNEL_13_THRESHOLD_TYPE,
                CHANNEL_23_THRESHOLD_TYPE,
                INDIVIDUAL_THRESHOLD_TYPE,
            ),
            state="readonly",
            width=38,
        ).grid(row=0, column=3, sticky="ew")

        self._help_label(
            threshold_finder, "Other project", "Used only when Sections is set to Chosen project file — all entries."
        ).grid(row=1, column=0, sticky="w", padx=(0, 5), pady=(8, 0))
        ttk.Entry(threshold_finder, textvariable=self.values["threshold_project"]).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 5), pady=(8, 0)
        )
        ttk.Button(threshold_finder, text="Browse…", command=self._browse_threshold_project).grid(
            row=1, column=3, sticky="e", pady=(8, 0)
        )

        automatic_guard_frame = ttk.Frame(threshold_finder)
        automatic_guard_frame.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            automatic_guard_frame,
            text="Automatic background/positive separation guard",
            variable=self.values["automatic_background_guard"],
        ).pack(side="left")
        automatic_guard_help = tk.Label(
            automatic_guard_frame,
            text="?",
            width=2,
            foreground="#10A37F",
            cursor="question_arrow",
            font=("Segoe UI", 9, "bold"),
        )
        automatic_guard_help.pack(side="left")
        ToolTip(
            automatic_guard_help,
            "Automatically estimates the background/positive split from the dataset-wide intensity distribution, rejects saturated low-threshold plateaus, and warns when separation is weak.",
        )

        threshold_lock_frame = ttk.Frame(threshold_finder)
        threshold_lock_frame.grid(row=2, column=2, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Checkbutton(
            threshold_lock_frame,
            text="Lock optimized thresholds",
            variable=self.values["threshold_locked"],
            command=self._threshold_lock_changed,
        ).pack(side="left")
        threshold_lock_help = tk.Label(
            threshold_lock_frame, text="?", width=2, foreground="#10A37F",
            cursor="question_arrow", font=("Segoe UI", 9, "bold")
        )
        threshold_lock_help.pack(side="left")
        ToolTip(
            threshold_lock_help,
            "Locks optimized Channel 1, Channel 2, and Channel 3 thresholds so the same values are used for every section.",
        )

        self.threshold_button = ttk.Button(
            threshold_finder,
            text="FIND GLOBAL THRESHOLD",
            command=self._start_threshold_optimization,
            style="Accent.TButton",
        )
        self.threshold_button.grid(row=3, column=2, columnspan=2, sticky="e", pady=(12, 0))
        self.threshold_advanced_button = ttk.Button(
            threshold_finder,
            text="Advanced plateau settings…",
            command=self._show_advanced_threshold_window,
        )
        self.threshold_advanced_button.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))

        controls = ttk.Frame(root, padding=(10, 8), style="Surface.TFrame")
        controls.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.validate_all_button = ttk.Button(
            controls,
            text="VALIDATE",
            command=self._start_validation_batch,
            style="Action.TButton",
        )
        self.validate_all_button.pack(side="left")
        self.batch_analyze_button = ttk.Button(
            controls,
            text="ANALYZE",
            command=self._choose_batch_images,
            style="Accent.TButton",
        )
        self.batch_analyze_button.pack(side="left", padx=(8, 8))
        project_safety_tip = (
            "Close the QuPath desktop window before VALIDATE or ANALYZE to prevent project conflicts. "
            "Threshold values are test settings until they have been scientifically validated."
        )
        ToolTip(self.validate_all_button, project_safety_tip)
        ToolTip(self.batch_analyze_button, project_safety_tip)
        self.cancel_button = ttk.Button(
            controls,
            text="Cancel",
            command=self._cancel,
            state="disabled",
            style="Danger.TButton",
        )
        self.cancel_button.pack(side="left")
        self.results_button = ttk.Button(controls, text="Open results folder", command=self._open_results, state="disabled")
        self.results_button.pack(side="right")
        self.log_button = ttk.Button(controls, text="Open run log", command=self._show_log_window)
        self.log_button.pack(side="right", padx=(0, 8))
        progress_area = ttk.Frame(controls)
        progress_area.pack(side="left", fill="x", expand=True, padx=12)
        progress_header = ttk.Frame(progress_area)
        progress_header.pack(fill="x")
        self.run_state_badge = ttk.Label(
            progress_header,
            textvariable=self.run_state_text,
            style="StatusReady.TLabel",
        )
        self.run_state_badge.pack(side="left")
        ttk.Label(progress_header, textvariable=self.progress_text, anchor="center").pack(
            side="left", fill="x", expand=True, padx=(8, 0)
        )
        self.progress = ttk.Progressbar(
            progress_area,
            mode="determinate",
            maximum=100,
            variable=self.progress_value,
        )
        self.progress.pack(fill="x", pady=(2, 0))
        self._build_log_window()
        self.bind_all("<Control-o>", lambda _event: self._browse_project())
        self.bind_all("<Control-l>", lambda _event: self._show_log_window())
        self.bind_all("<F1>", lambda _event: self._show_help_window())

    def _build_log_window(self) -> None:
        self.log_window = tk.Toplevel(self)
        self.log_window.title(f"Brain Cell Counter {APP_VERSION_LABEL} — Run log")
        self.log_window.geometry("980x560")
        self.log_window.minsize(700, 360)
        self.log_window.protocol("WM_DELETE_WINDOW", self.log_window.withdraw)

        frame = ttk.Frame(self.log_window, padding=10)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            frame,
            wrap="word",
            state="disabled",
            font=("Consolas", int(self.values["log_font_size"].get())),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        ttk.Button(frame, text="Close log", command=self.log_window.withdraw).grid(
            row=1, column=0, columnspan=2, sticky="e", pady=(8, 0)
        )
        self.log_window.withdraw()

    def _show_log_window(self) -> None:
        if self.log_window is None or not self.log_window.winfo_exists():
            self._build_log_window()
        assert self.log_window is not None
        self.log_window.deiconify()
        self.log_window.lift()

    def _apply_ui_preferences(self, show_error: bool = True) -> bool:
        try:
            font_size = int(self.values["ui_font_size"].get())
            log_font_size = int(self.values["log_font_size"].get())
            if not 8 <= font_size <= 18:
                raise ValueError("Interface font size must be between 8 and 18.")
            if not 8 <= log_font_size <= 18:
                raise ValueError("Run-log font size must be between 8 and 18.")
            preset = self.values["window_size_preset"].get()
            if preset not in WINDOW_SIZE_PRESETS:
                raise ValueError("Choose a valid window-size preset.")
        except (TypeError, ValueError) as exc:
            if show_error:
                messagebox.showerror("Settings", str(exc))
            else:
                self.values["ui_font_size"].set("9")
                self.values["log_font_size"].set("9")
                self.values["window_width"].set("1080")
                self.values["window_height"].set("820")
                self.values["window_size_preset"].set(WINDOW_SIZE_RECOMMENDED)
            return False

        for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(font_name).configure(size=font_size)
        tkfont.nametofont("TkFixedFont").configure(size=log_font_size)
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        if preset == WINDOW_SIZE_MAXIMIZED:
            self.values["window_width"].set(str(screen_width))
            self.values["window_height"].set(str(screen_height))
            self.state("zoomed")
        else:
            if preset == WINDOW_SIZE_RECOMMENDED:
                window_width = max(1000, min(1280, screen_width - 80))
                window_height = max(760, min(900, screen_height - 120))
            elif preset == WINDOW_SIZE_COMPACT:
                window_width, window_height = 1080, 820
            else:
                window_width, window_height = 1440, 960
            window_width = min(window_width, max(980, screen_width - 20))
            window_height = min(window_height, max(740, screen_height - 60))
            self.values["window_width"].set(str(window_width))
            self.values["window_height"].set(str(window_height))
            self.state("normal")
            self.geometry(f"{window_width}x{window_height}")
        if self.log_text is not None and self.log_text.winfo_exists():
            self.log_text.configure(font=("Consolas", log_font_size))
        return True

    def _show_settings_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("Brain Cell Counter — Settings")
        window.geometry("760x455")
        window.minsize(650, 410)
        window.transient(self)

        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        self._help_label(
            frame,
            "QuPath Console",
            "The QuPath console executable used for validation, Cellpose processing, project saving, and result export.",
        ).grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.values["qupath"]).grid(row=0, column=1, sticky="ew", padx=8, pady=5)
        ttk.Button(frame, text="Browse...", command=self._browse_qupath).grid(row=0, column=2, pady=5)

        self._help_label(
            frame,
            "Cellpose Python",
            "The python.exe file from the Cellpose environment. CUDA-enabled PyTorch is recommended for GPU processing.",
        ).grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.values["cellpose_python"]).grid(
            row=1, column=1, sticky="ew", padx=8, pady=5
        )
        ttk.Button(frame, text="Browse...", command=self._browse_cellpose_python).grid(row=1, column=2, pady=5)

        self._help_label(
            frame,
            "Fiji",
            "The Fiji launcher used to export human-confirmed calibrated copies as verified OME-TIFF files.",
        ).grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.values["fiji"]).grid(row=2, column=1, sticky="ew", padx=8, pady=5)
        ttk.Button(frame, text="Browse...", command=self._browse_fiji).grid(row=2, column=2, pady=5)

        display = ttk.LabelFrame(frame, text="Display", padding=10)
        display.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        for column in (1, 3):
            display.columnconfigure(column, weight=1)
        display_fields = (
            ("Interface font size", "ui_font_size", 0, 0),
            ("Run-log font size", "log_font_size", 0, 2),
        )
        for label, key, row, column in display_fields:
            ttk.Label(display, text=label).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=5)
            ttk.Entry(display, textvariable=self.values[key], width=10).grid(
                row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=5
            )
        self._help_label(
            display,
            "Window size",
            "Recommended automatically fits the current monitor. Compact saves screen space, Large provides more room, and Maximized fills the available desktop.",
        ).grid(row=1, column=0, sticky="w", padx=(0, 6), pady=5)
        ttk.Combobox(
            display,
            textvariable=self.values["window_size_preset"],
            values=WINDOW_SIZE_PRESETS,
            state="readonly",
            width=38,
        ).grid(row=1, column=1, columnspan=3, sticky="ew", padx=(0, 14), pady=5)

        ttk.Label(
            frame,
            text="Settings are saved in brain_cell_counter_settings.json and restored at the next launch.",
            foreground="#555555",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        def save_and_apply() -> None:
            if not self._apply_ui_preferences(show_error=True):
                return
            self._save_channel_names(show_confirmation=False)
            messagebox.showinfo("Settings", "Settings were saved and applied.", parent=window)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")
        ttk.Button(buttons, text="Save & Apply", command=save_and_apply).pack(side="right", padx=(0, 8))

    def _show_help_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("Brain Cell Counter — Help and Methods")
        window.geometry("920x720")
        window.minsize(700, 500)
        window.transient(self)
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text_widget = tk.Text(
            frame,
            wrap="word",
            padx=14,
            pady=12,
            font=tkfont.nametofont("TkTextFont"),
        )
        text_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, command=text_widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text_widget.configure(yscrollcommand=scrollbar.set)
        help_text = f"""BRAIN CELL COUNTER {APP_VERSION_LABEL} — USER GUIDE

PURPOSE
This application connects an ABBA/DeepSlice-registered QuPath project to Cellpose-SAM and exports cell counts for Allen Brain Atlas regions.

REQUIRED SOFTWARE AND DATA
1. Fiji/ImageJ with ABBA for atlas registration and QuPath export.
2. DeepSlice Local for section-position prediction when used in the registration workflow.
3. QuPath 0.7 with the ABBA and BIOP Cellpose extensions.
4. A Python Cellpose environment containing Cellpose-SAM, PyTorch, and preferably CUDA support.
5. A QuPath .qpproj containing image entries, ABBA ROIs/transforms, the Allen ontology JSON, and accessible source images.
6. An NVIDIA CUDA-compatible GPU is recommended. The current Cellpose environment is configured for CUDA on the RTX 3050.

STANDARD WORKFLOW
1. Complete ABBA/DeepSlice registration and export the atlas annotations to QuPath.
2. If ABBA registration files were exported but atlas annotations were not loaded, click IMPORT ABBA. The application imports registered entries sequentially and reports unregistered entries without copying transforms between slices.
3. Close the QuPath desktop window to avoid simultaneous project writes.
4. Select project.qpproj in the main window.
5. Open Settings, verify QuPath Console and Cellpose Python, and choose a window-size preset. Recommended automatically fits the monitor.
6. In ANALYSIS, set channel names, intensity thresholds, detection reuse, and hemisphere mode. Open advanced Cellpose settings only when Cellprob, Flow, or automatic suggestion is needed.
7. Run VALIDATE. It checks every image entry sequentially without saving changes.
8. Optionally run AUTO SUGGEST CELLPOSE and FIND GLOBAL THRESHOLD.
9. Run ANALYZE, select image entries, and confirm. Entries run strictly from top to bottom.
10. Review the run log, regional CSV files, atlas-overlay PNG files, and saved QuPath detections.

BATCH VALIDATION AND ANALYSIS
VALIDATE checks every project image entry. A failed entry is logged and does not stop later entries.
ANALYZE opens a multi-select list. The selected entries are sorted by project order. Each entry completes validation, Cellpose, classification, export, and QuPath saving before the next entry starts. Only one QuPath/Cellpose process runs at a time to protect GPU memory and project integrity.

PROJECT PREFLIGHT AND LINK REPAIR
PREFLIGHT checks the source-image URI stored for every QuPath entry before analysis. Accessible links are marked OK and broken links are marked MISSING in the run log. If links are broken, choose a folder containing the moved images. The application searches recursively by exact image filename and proposes only unique matches. It never changes ambiguous or unresolved links. Before any confirmed repair, it creates a timestamped backup beside project.qpproj and then reloads the project.

BATCH IMAGE CALIBRATION
CALIBRATE accepts many files through ADD IMAGES, ADD FOLDER, or ADD ALL PROJECT IMAGES. QuPath generates one bounded QC preview at a time. The software proposes a bright scale-bar line near an image margin, but the reviewer must verify or redraw both endpoints, enter the printed physical distance and unit, and click CONFIRM & NEXT. Unconfirmed rows cannot be applied. APPLY CONFIRMED TO QUPATH displays a final list, creates project and data.qpdata backups, and updates QuPath metadata only. Source TIFF pixels are never resized or overwritten. A CSV audit manifest and QC previews are saved under brain_cell_counter_calibration.

EXPORT CALIBRATED WITH FIJI does not require a QuPath entry match. It writes each confirmed image to a new _calibrated.ome.tif file, then reopens the output and verifies dimensions, channels, slices, frames, bit depth, and physical pixel size. Existing source and calibrated files are never overwritten. Fiji outputs and JSON verification reports are stored under brain_cell_counter_calibration.

ABBA ATLAS IMPORT
IMPORT ABBA scans every project entry for its own ABBA-RoiSet and ABBA-Transform files plus the project ontology. Complete entries are imported and saved sequentially using the selected Left and Right or Do not split mode. Incomplete entries are skipped and reported. This step imports existing registrations only; it never copies a transform from another slice and cannot replace ABBA/DeepSlice registration.

RESUME INTERRUPTED ANALYSIS
After every successfully analyzed entry, the application writes a versioned checkpoint to brain_cell_counter_results. The checkpoint is tied to the exact QuPath project, application output schema, and analysis parameters. When ANALYZE is started again with matching settings, the application offers to skip already completed selected entries or rerun all selected entries. Changing any analysis parameter is treated as a new run, so entries from incompatible settings are not reused.

CELLPOSE PARAMETER SUGGESTION
AUTO SUGGEST CELLPOSE tests 15 combinations: five Cellprob values around the current value and three Flow values around the current value. It uses three representative 640 × 640-pixel crops. Candidates losing more than half of the maximum masks are rejected. The locally most stable mask-count result is suggested, with ties favoring higher Cellprob and lower Flow. The test does not save temporary ROIs or project changes.

GLOBAL THRESHOLD PROFILE
The threshold scan uses existing Cellpose detections and each detection's mean intensity for Channel 1, Channel 2, and Channel 3. Cellpose is not rerun during threshold optimization.

For threshold T:
TotalChange(T) = abs(TotalCount(T) - TotalCount(T-step)) / TotalCount(T-step) × 100

For section i:
SectionChange_i(T) = abs(Count_i(T) - Count_i(T-step)) / Count_i(T-step) × 100

A step is stable when TotalChange is below the configured total limit and the configured percentage of sections has SectionChange below the section limit. A plateau requires the configured minimum number of consecutive stable steps. Without the automatic guard, Individual mode selects the highest threshold in the longest qualifying plateau.

INDIVIDUAL CHANNEL MODE
Each channel is evaluated independently. While Channel 1 is scanned, Channel 2 and Channel 3 intensities do not affect the count. The same rule applies independently to Channels 2 and 3.

DOUBLE-POSITIVE PAIR MODES
Available pairs are Channel 1+2, Channel 1+3, and Channel 2+3. For thresholds Ta and Tb, a detection is double-positive only when intensity_a >= Ta AND intensity_b >= Tb. The software evaluates all threshold pairs allowed by both channel plateaus and selects the pair with the greatest dataset-wide double-positive count. Equal counts favor the higher, more selective thresholds.

AUTOMATIC BACKGROUND/POSITIVE GUARD
The guard reconstructs an intensity histogram from the cumulative threshold-count curve and applies Otsu between-class variance separation independently to each channel. Thresholds below the estimated negative/positive split are excluded, preventing a saturated low-threshold plateau in which nearly every DAPI detection appears marker-positive. The report records the split threshold, positive fraction, separability score, selection source, and warnings. If no stable plateau remains after background rejection, the Otsu split is used as a clearly labeled fallback. Weak separation must be reviewed visually.

For candidate split T:
OtsuScore(T) = weight_negative(T) × weight_positive(T) × (mean_negative(T) - mean_positive(T))²
AutomaticSplit = the T that maximizes OtsuScore(T)
SeparabilityScore = maximum between-class variance / total intensity variance

OUTPUTS
Each analyzed entry produces a regional-count CSV and a high-resolution atlas-overlay PNG. CSV rows contain Allen region information, hemisphere, Major Group, areas, detection totals, positive-class totals, and densities. Threshold optimization additionally produces CSV, JSON, and HTML audit reports, including the selected pair matrix when pair mode is used.

RESULT INSPECTION IN QUPATH
In RESULTS, choose a regional CSV and click OPEN IN QUPATH. The application reads the exact image-entry name from the CSV and opens that saved entry in the normal QuPath desktop viewer. The button is unavailable during headless project operations. Close QuPath before starting another operation that writes the project.

COMPACT RESULT EXPORT
RESULTS starts with the Essential preset: image and atlas identifiers, hemisphere, area, and cell counts. Choose another preset or open CHOOSE COLUMNS to tick individual measurements, then click EXPORT VISIBLE CSV to create a smaller table containing exactly those columns. The complete versioned master CSV is preserved unchanged.

IMPORTANT LIMITATIONS
Automatic thresholds are proposals, not biological ground truth. Always review Cellpose masks and marker classification visually. Do not force cFOS and NeuN counts to be equal. Broken image URIs must be repaired with PREFLIGHT or QuPath before validation or analysis can open those entries.
"""
        text_widget.insert("1.0", help_text)
        text_widget.configure(state="disabled")
        ttk.Button(frame, text="Close", command=window.destroy).grid(row=1, column=0, columnspan=2, sticky="e", pady=(8, 0))

    def _browse_qupath(self) -> None:
        path = filedialog.askopenfilename(title="Choose QuPath console executable", filetypes=[("Executable", "*.exe")])
        if path:
            self.values["qupath"].set(path)

    def _browse_project(self) -> None:
        path = filedialog.askopenfilename(title="Choose QuPath project", filetypes=[("QuPath project", "*.qpproj")])
        if path:
            self.values["project"].set(path)
            self._load_project()
            self._save_channel_names(show_confirmation=False)

    def _browse_threshold_project(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose QuPath project for global threshold optimization",
            filetypes=[("QuPath project", "*.qpproj")],
        )
        if path:
            self.values["threshold_project"].set(path)
            self.values["threshold_scope"].set("Chosen project file — all entries")

    def _browse_cellpose_python(self) -> None:
        path = filedialog.askopenfilename(title="Choose Cellpose Python executable", filetypes=[("Python", "python.exe")])
        if path:
            self.values["cellpose_python"].set(path)

    def _browse_fiji(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose Fiji launcher",
            filetypes=[("Fiji executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.values["fiji"].set(path)

    @staticmethod
    def _file_uri_to_path(uri: str) -> Path | None:
        parsed = urlparse(uri)
        if parsed.scheme.lower() != "file":
            return None
        raw_path = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", raw_path):
            raw_path = raw_path[1:]
        return Path(raw_path.replace("/", os.sep))

    @classmethod
    def _find_entry_uri_container(cls, value: object) -> tuple[dict, str] | None:
        if isinstance(value, dict):
            uri = value.get("uri")
            if isinstance(uri, str) and uri.lower().startswith("file:"):
                return value, "uri"
            for nested in value.values():
                found = cls._find_entry_uri_container(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = cls._find_entry_uri_container(nested)
                if found is not None:
                    return found
        return None

    def _scan_project_sources(self, project_path: Path) -> tuple[dict, list[dict[str, object]]]:
        data = json.loads(project_path.read_text(encoding="utf-8-sig"))
        results: list[dict[str, object]] = []
        for entry in data.get("images", []):
            image_name = str(entry.get("imageName") or "Unnamed entry")
            container = self._find_entry_uri_container(entry.get("serverBuilder", {}))
            uri = container[0][container[1]] if container is not None else ""
            source_path = self._file_uri_to_path(uri) if uri else None
            results.append({
                "entry": entry,
                "image_name": image_name,
                "container": container,
                "uri": uri,
                "source_path": source_path,
                "exists": bool(source_path and source_path.is_file()),
            })
        return data, results

    def _run_project_preflight(self) -> None:
        project_path = Path(self.values["project"].get().strip())
        if not project_path.is_file():
            messagebox.showerror("Project preflight", "Choose an existing QuPath project first.")
            return
        try:
            project_data, scan = self._scan_project_sources(project_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            messagebox.showerror("Project preflight", f"Could not inspect the QuPath project:\n{exc}")
            return
        missing = [item for item in scan if not item["exists"]]
        self._show_log_window()
        self._append_log("=" * 72)
        self._append_log(f"PROJECT PREFLIGHT: {project_path}")
        for item in scan:
            status = "OK" if item["exists"] else "MISSING"
            self._append_log(f"{status}: {item['image_name']} -> {item['source_path'] or item['uri'] or 'No file URI'}")
        if not missing:
            messagebox.showinfo(
                "Project preflight",
                f"All {len(scan)} project image entries have accessible source files.",
            )
            return

        if not messagebox.askyesno(
            "Project preflight",
            f"{len(missing)} of {len(scan)} image entries have missing source files.\n\n"
            "Search a folder recursively for files with the same names and propose repaired links?",
        ):
            return
        search_folder = filedialog.askdirectory(title="Choose the folder containing the moved source images")
        if not search_folder:
            return
        search_root = Path(search_folder)
        proposed: list[tuple[dict[str, object], Path]] = []
        ambiguous: list[str] = []
        unresolved: list[str] = []
        for item in missing:
            image_name = str(item["image_name"])
            source_path = item.get("source_path")
            source_filename = source_path.name if isinstance(source_path, Path) else image_name
            matches = [path for path in search_root.rglob(source_filename) if path.is_file()]
            if len(matches) == 1 and item["container"] is not None:
                proposed.append((item, matches[0].resolve()))
            elif len(matches) > 1:
                ambiguous.append(image_name)
            else:
                unresolved.append(image_name)
        proposal_lines = [f"{item['image_name']}\n  -> {path}" for item, path in proposed]
        details = "\n\n".join(proposal_lines) if proposal_lines else "No unique file matches were found."
        if ambiguous:
            details += "\n\nMultiple matches (not changed):\n- " + "\n- ".join(ambiguous)
        if unresolved:
            details += "\n\nNot found:\n- " + "\n- ".join(unresolved)
        if not proposed:
            messagebox.showwarning("Project preflight", details)
            return
        if not messagebox.askyesno(
            "Apply repaired image links",
            f"The following {len(proposed)} unique replacement link(s) were found:\n\n{details}\n\n"
            "Create a backup of project.qpproj and apply these links?",
        ):
            return
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = project_path.with_name(f"{project_path.name}.preflight-backup-{timestamp}.bak")
            shutil.copy2(project_path, backup_path)
            for item, replacement in proposed:
                container, key = item["container"]
                container[key] = replacement.as_uri()
            temporary_path = project_path.with_name(project_path.name + ".preflight.tmp")
            temporary_path.write_text(
                json.dumps(project_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, project_path)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Project preflight", f"Could not repair project links:\n{exc}")
            return
        self._append_log(f"Repaired {len(proposed)} image link(s). Backup: {backup_path}")
        self._load_project()
        messagebox.showinfo(
            "Project preflight",
            f"Repaired {len(proposed)} image link(s).\n\nBackup created:\n{backup_path}",
        )

    def _load_saved_channel_names(self) -> None:
        if not SETTINGS_FILE.is_file():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            saved_project = data.get("project")
            if isinstance(saved_project, str) and Path(saved_project).is_file():
                self.values["project"].set(saved_project)
            saved_qupath = data.get("qupath_path")
            if isinstance(saved_qupath, str) and saved_qupath.strip():
                self.values["qupath"].set(saved_qupath.strip())
            saved_cellpose_python = data.get("cellpose_python_path")
            if isinstance(saved_cellpose_python, str) and saved_cellpose_python.strip():
                self.values["cellpose_python"].set(saved_cellpose_python.strip())
            saved_fiji = data.get("fiji_path")
            if isinstance(saved_fiji, str) and saved_fiji.strip():
                self.values["fiji"].set(saved_fiji.strip())
            names = data.get("channel_names", {})
            for index in range(1, 4):
                saved_name = names.get(str(index))
                if isinstance(saved_name, str) and saved_name.strip():
                    self.values[f"channel_{index}_name"].set(saved_name.strip())
            saved_hemisphere_mode = data.get("hemisphere_mode")
            if saved_hemisphere_mode in {"Left and Right", "Do not split"}:
                self.values["hemisphere_mode"].set(saved_hemisphere_mode)
            saved_thresholds = data.get("global_thresholds", {})
            if saved_thresholds.get("channel1") is not None:
                self.values["neun_threshold"].set(str(saved_thresholds["channel1"]))
            if saved_thresholds.get("channel2") is not None:
                self.values["cfos_threshold"].set(str(saved_thresholds["channel2"]))
            if saved_thresholds.get("channel3") is not None:
                self.values["dapi_threshold"].set(str(saved_thresholds["channel3"]))
            self.values["threshold_locked"].set(bool(saved_thresholds.get("locked", False)))
            saved_cellpose = data.get("cellpose_parameters", {})
            if saved_cellpose.get("cellprob") is not None:
                self.values["cellprob"].set(str(saved_cellpose["cellprob"]))
            if saved_cellpose.get("flow") is not None:
                self.values["flow"].set(str(saved_cellpose["flow"]))
            ui_settings = data.get("ui_settings", {})
            ui_setting_map = {
                "font_size": "ui_font_size",
                "log_font_size": "log_font_size",
                "window_width": "window_width",
                "window_height": "window_height",
                "window_size_preset": "window_size_preset",
            }
            for saved_key, value_key in ui_setting_map.items():
                if ui_settings.get(saved_key) is not None:
                    self.values[value_key].set(str(ui_settings[saved_key]))
            optimizer = data.get("threshold_optimizer", {})
            if optimizer.get("automatic_background_guard") is not None:
                self.values["automatic_background_guard"].set(
                    bool(optimizer["automatic_background_guard"])
                )
            saved_objective = optimizer.get("objective")
            if saved_objective == "Maximize Channel 1 + Channel 2 overlap":
                saved_objective = CHANNEL_12_THRESHOLD_TYPE
            elif saved_objective == "Independent stable plateaus":
                saved_objective = INDIVIDUAL_THRESHOLD_TYPE
            if saved_objective in {
                INDIVIDUAL_THRESHOLD_TYPE,
                CHANNEL_12_THRESHOLD_TYPE,
                CHANNEL_13_THRESHOLD_TYPE,
                CHANNEL_23_THRESHOLD_TYPE,
            }:
                self.values["threshold_objective"].set(saved_objective)
            saved_scope = optimizer.get("scope")
            if saved_scope == "Batch folder — all projects":
                saved_scope = "Chosen project file — all entries"
            if saved_scope in {
                "Current project — all entries",
                "Selected entry only — test",
                "Chosen project file — all entries",
            }:
                self.values["threshold_scope"].set(saved_scope)
            saved_threshold_project = optimizer.get("project")
            if not saved_threshold_project:
                legacy_folder = optimizer.get("batch_root")
                if legacy_folder:
                    legacy_project = Path(str(legacy_folder)) / "project.qpproj"
                    if legacy_project.is_file():
                        saved_threshold_project = str(legacy_project)
            if saved_threshold_project:
                self.values["threshold_project"].set(str(saved_threshold_project))
            setting_map = {
                "channel1_start": "channel1_search_start",
                "channel1_end": "channel1_search_end",
                "channel1_step": "channel1_search_step",
                "channel2_start": "channel2_search_start",
                "channel2_end": "channel2_search_end",
                "channel2_step": "channel2_search_step",
                "channel3_start": "channel3_search_start",
                "channel3_end": "channel3_search_end",
                "channel3_step": "channel3_search_step",
                "total_change_limit": "total_change_limit",
                "section_change_limit": "section_change_limit",
                "stable_section_percent": "stable_section_percent",
                "minimum_stable_steps": "minimum_stable_steps",
            }
            for saved_key, value_key in setting_map.items():
                if optimizer.get(saved_key) is not None:
                    self.values[value_key].set(str(optimizer[saved_key]))
        except (OSError, ValueError, TypeError):
            # Invalid optional settings must not prevent the analysis GUI opening.
            return

    def _channel_names(self) -> list[str]:
        names = [self.values[f"channel_{index}_name"].get().strip() for index in range(1, 4)]
        if any(not name for name in names):
            raise ValueError("Each channel must have a non-empty name.")
        return names

    def _save_channel_names(self, show_confirmation: bool = True) -> None:
        try:
            names = self._channel_names()
            SETTINGS_FILE.write_text(
                json.dumps(
                    {
                        "project": self.values["project"].get().strip(),
                        "qupath_path": self.values["qupath"].get().strip(),
                        "cellpose_python_path": self.values["cellpose_python"].get().strip(),
                        "fiji_path": self.values["fiji"].get().strip(),
                        "channel_names": {str(index + 1): name for index, name in enumerate(names)},
                        "hemisphere_mode": self.values["hemisphere_mode"].get(),
                        "global_thresholds": {
                            "channel1": self.values["neun_threshold"].get(),
                            "channel2": self.values["cfos_threshold"].get(),
                            "channel3": self.values["dapi_threshold"].get(),
                            "locked": bool(self.values["threshold_locked"].get()),
                        },
                        "cellpose_parameters": {
                            "cellprob": self.values["cellprob"].get(),
                            "flow": self.values["flow"].get(),
                        },
                        "ui_settings": {
                            "font_size": self.values["ui_font_size"].get(),
                            "log_font_size": self.values["log_font_size"].get(),
                            "window_width": self.values["window_width"].get(),
                            "window_height": self.values["window_height"].get(),
                            "window_size_preset": self.values["window_size_preset"].get(),
                        },
                        "threshold_optimizer": {
                            "objective": self.values["threshold_objective"].get(),
                            "automatic_background_guard": bool(
                                self.values["automatic_background_guard"].get()
                            ),
                            "scope": self.values["threshold_scope"].get(),
                            "project": self.values["threshold_project"].get(),
                            "channel1_start": self.values["channel1_search_start"].get(),
                            "channel1_end": self.values["channel1_search_end"].get(),
                            "channel1_step": self.values["channel1_search_step"].get(),
                            "channel2_start": self.values["channel2_search_start"].get(),
                            "channel2_end": self.values["channel2_search_end"].get(),
                            "channel2_step": self.values["channel2_search_step"].get(),
                            "channel3_start": self.values["channel3_search_start"].get(),
                            "channel3_end": self.values["channel3_search_end"].get(),
                            "channel3_step": self.values["channel3_search_step"].get(),
                            "total_change_limit": self.values["total_change_limit"].get(),
                            "section_change_limit": self.values["section_change_limit"].get(),
                            "stable_section_percent": self.values["stable_section_percent"].get(),
                            "minimum_stable_steps": self.values["minimum_stable_steps"].get(),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
        except (OSError, ValueError) as exc:
            if show_confirmation:
                messagebox.showerror("Channel names", f"Could not save channel names:\n{exc}")
            return
        if show_confirmation:
            messagebox.showinfo("Channel names", "Channel names were saved and will be restored next time.")

    def _load_project(self) -> None:
        project_path = Path(self.values["project"].get().strip())
        if not project_path.is_file():
            messagebox.showerror("Project", "Choose an existing .qpproj file.")
            return
        try:
            data = json.loads(project_path.read_text(encoding="utf-8-sig"))
            entries = {
                str(item["imageName"]): int(item["entryID"])
                for item in data.get("images", [])
                if "imageName" in item and "entryID" in item
            }
        except Exception as exc:
            messagebox.showerror("Project", f"Could not read the QuPath project:\n{exc}")
            return
        self.project_entries = entries
        names = list(entries)
        self.image_combo["values"] = names
        if names:
            self.values["image"].set(names[0])
        self.last_output_folder = project_path.parent / "brain_cell_counter_results"
        self.results_preview.set_project(project_path)
        self._update_local_status()

    def _update_local_status(self) -> None:
        project_path = Path(self.values["project"].get().strip())
        image_name = self.values["image"].get()
        entry_id = self.project_entries.get(image_name)
        if entry_id is None:
            self.local_status.configure(text="Image entry is not available.", foreground="#9a0000")
            project_label = project_path.name if project_path.is_file() else "No project selected"
            self.project_context.configure(text=project_label)
            return
        entry_folder = project_path.parent / "data" / str(entry_id)
        roi = list(entry_folder.glob("ABBA-RoiSet-*"))
        transform = list(entry_folder.glob("ABBA-Transform-*"))
        ontology = list(project_path.parent.glob("*-Ontology.json"))
        ready = bool(roi and transform and ontology)
        text = (
            f"Entry {entry_id}: ABBA ROI={'yes' if roi else 'no'}, transform={'yes' if transform else 'no'}, "
            f"ontology={'yes' if ontology else 'no'}"
        )
        self.local_status.configure(text=text, foreground="#147a2e" if ready else "#9a0000")
        readiness = "ABBA ready" if ready else "Preparation required"
        self.project_context.configure(
            text=f"{project_path.name}  •  {len(self.project_entries)} entries  •  {image_name}  •  {readiness}"
        )

    def _show_calibration_wizard(self) -> None:
        qupath = Path(self.values["qupath"].get().strip())
        project = Path(self.values["project"].get().strip())
        if not qupath.is_file() or not project.is_file():
            messagebox.showerror(
                "Batch calibration",
                "Choose an existing QuPath console executable and project.qpproj first.",
            )
            return
        CalibrationWizard(
            self,
            qupath,
            Path(self.values["fiji"].get().strip()),
            project,
            self._set_calibration_busy,
        )

    def _set_calibration_busy(self, running: bool) -> None:
        """Prevent QuPath/Cellpose jobs while the calibration wizard owns QuPath."""
        self.is_running = running
        if running:
            for attribute in ("advanced_cellpose_window", "advanced_threshold_window"):
                window = getattr(self, attribute)
                if window is not None and window.winfo_exists():
                    window.destroy()
                setattr(self, attribute, None)
        state = "disabled" if running else "normal"
        self.validate_all_button.configure(state=state)
        self.batch_analyze_button.configure(state=state)
        self.import_abba_button.configure(state=state)
        self.calibration_button.configure(state=state)
        self.calibrate_button.configure(state=state)
        self.cellpose_advanced_button.configure(state=state)
        self.threshold_button.configure(state=state)
        self.threshold_advanced_button.configure(state=state)
        self.results_preview.set_busy(running)
        self.cancel_button.configure(state="disabled")
        if running:
            self._set_progress(2, "Calibration wizard is using QuPath", allow_decrease=True)
        else:
            self._set_progress(0, "Ready", allow_decrease=True)

    @staticmethod
    def _scan_abba_registrations(
        project_path: Path,
        entries: dict[str, int],
    ) -> tuple[list[str], list[dict[str, object]]]:
        """Return registered image names and diagnostics without changing the project."""
        ontology_present = any(project_path.parent.glob("*-Ontology.json"))
        registered: list[str] = []
        diagnostics: list[dict[str, object]] = []
        for image_name, entry_id in entries.items():
            entry_folder = project_path.parent / "data" / str(entry_id)
            has_roi = any(entry_folder.glob("ABBA-RoiSet-*"))
            has_transform = any(entry_folder.glob("ABBA-Transform-*"))
            ready = has_roi and has_transform and ontology_present
            diagnostics.append({
                "image": image_name,
                "entry_id": entry_id,
                "roi": has_roi,
                "transform": has_transform,
                "ontology": ontology_present,
                "ready": ready,
            })
            if ready:
                registered.append(image_name)
        return registered, diagnostics

    def _start_abba_import_batch(self) -> None:
        qupath = Path(self.values["qupath"].get().strip())
        project = Path(self.values["project"].get().strip())
        engine = Path(__file__).resolve().parent.parent / "Brain_Cell_Counter_QuPath_Engine_v1.0.groovy"
        if not qupath.is_file() or not project.is_file() or not engine.is_file():
            messagebox.showerror("Missing input", "Check the QuPath executable, project, and analysis engine file.")
            return
        registered, diagnostics = self._scan_abba_registrations(project, self.project_entries)
        missing = [item for item in diagnostics if not item["ready"]]
        if not registered:
            details = "\n".join(
                f"- {item['image']}: ROI={'yes' if item['roi'] else 'no'}, "
                f"transform={'yes' if item['transform'] else 'no'}, "
                f"ontology={'yes' if item['ontology'] else 'no'}"
                for item in missing
            )
            messagebox.showerror(
                "Import ABBA",
                "No project entry has a complete exported ABBA registration.\n\n"
                "Run ABBA/DeepSlice registration and export the slices to this QuPath project first.\n\n"
                + details,
            )
            return
        try:
            arguments = self._parameters("apply-atlas")
        except ValueError as exc:
            messagebox.showerror("Invalid parameters", str(exc))
            return
        missing_text = ""
        if missing:
            missing_text = (
                f"\n\n{len(missing)} entry/entries do not have a complete exported registration and will be skipped."
            )
        if not messagebox.askyesno(
            "Import ABBA atlas annotations",
            f"Import and save ABBA atlas annotations for {len(registered)} registered entry/entries?\n\n"
            "Entries run sequentially in QuPath project order. Existing Cellpose detections are preserved."
            + missing_text
            + "\n\nClose the QuPath desktop window before continuing.",
        ):
            return
        self._save_channel_names(show_confirmation=False)
        self._show_log_window()
        self._append_log("=" * 72)
        self._append_log(f"ABBA import started: {len(registered)} registered project entry/entries")
        for item in missing:
            self._append_log(
                f"SKIPPED UNREGISTERED: {item['image']} "
                f"(ROI={'yes' if item['roi'] else 'no'}, transform={'yes' if item['transform'] else 'no'}, "
                f"ontology={'yes' if item['ontology'] else 'no'})"
            )
        self.batch_cancel_requested = False
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._run_entry_batch,
            args=(qupath, project, engine, arguments, registered, "apply-atlas", True),
            daemon=True,
        )
        self.worker.start()

    def _parameters(self, mode: str) -> list[str]:
        channel_names = self._channel_names()
        values = {
            "mode": mode,
            "neunChannel": int(self.values["neun_channel"].get()),
            "cfosChannel": int(self.values["cfos_channel"].get()),
            "dapiChannel": int(self.values["dapi_channel"].get()),
            "neunThreshold": float(self.values["neun_threshold"].get()),
            "cfosThreshold": float(self.values["cfos_threshold"].get()),
            "dapiThreshold": float(self.values["dapi_threshold"].get()),
            "requireDapiThreshold": str(bool(self.values["require_dapi"].get())).lower(),
            "cellprobThreshold": float(self.values["cellprob"].get()),
            "flowThreshold": float(self.values["flow"].get()),
            "reuseDetections": str(bool(self.values["reuse"].get())).lower(),
            "cellposePythonPath": self.values["cellpose_python"].get().strip(),
            "channel1Name": channel_names[0],
            "channel2Name": channel_names[1],
            "channel3Name": channel_names[2],
            "hemisphereMode": (
                "left-right"
                if self.values["hemisphere_mode"].get() == "Left and Right"
                else "do-not-split"
            ),
            "channel1ThresholdStart": float(self.values["channel1_search_start"].get()),
            "channel1ThresholdEnd": float(self.values["channel1_search_end"].get()),
            "channel1ThresholdStep": float(self.values["channel1_search_step"].get()),
            "channel2ThresholdStart": float(self.values["channel2_search_start"].get()),
            "channel2ThresholdEnd": float(self.values["channel2_search_end"].get()),
            "channel2ThresholdStep": float(self.values["channel2_search_step"].get()),
            "channel3ThresholdStart": float(self.values["channel3_search_start"].get()),
            "channel3ThresholdEnd": float(self.values["channel3_search_end"].get()),
            "channel3ThresholdStep": float(self.values["channel3_search_step"].get()),
            "thresholdPair": (
                "-".join(THRESHOLD_PAIR_BY_TYPE[self.values["threshold_objective"].get()])
                if self.values["threshold_objective"].get() in THRESHOLD_PAIR_BY_TYPE
                else "individual"
            ),
        }
        channels = [values["neunChannel"], values["cfosChannel"], values["dapiChannel"]]
        if min(channels) < 1:
            raise ValueError("Channel numbers are one-based and must be at least 1.")
        if not Path(str(values["cellposePythonPath"])).is_file():
            raise ValueError("Choose the existing python.exe from the Cellpose environment.")
        return [f"{key}={value}" for key, value in values.items()]

    def _threshold_lock_changed(self) -> None:
        self._update_threshold_lock_state()
        self._save_channel_names(show_confirmation=False)

    def _update_threshold_lock_state(self) -> None:
        state = "disabled" if self.values["threshold_locked"].get() else "normal"
        for key in ("neun_threshold", "cfos_threshold", "dapi_threshold"):
            entry = self.parameter_entries.get(key)
            if entry is not None:
                entry.configure(state=state)

    @staticmethod
    def _read_project_entries(project_path: Path) -> list[str]:
        data = json.loads(project_path.read_text(encoding="utf-8-sig"))
        return [
            str(item["imageName"])
            for item in data.get("images", [])
            if item.get("imageName")
        ]

    def _threshold_targets(self) -> tuple[list[dict[str, str]], Path]:
        scope = self.values["threshold_scope"].get()
        current_project = Path(self.values["project"].get().strip())
        if not current_project.is_file():
            raise ValueError("Choose an existing QuPath project.")

        projects: list[Path]
        selected_only = scope == "Selected entry only — test"
        if scope == "Chosen project file — all entries":
            threshold_project = Path(self.values["threshold_project"].get().strip())
            if not threshold_project.is_file() or threshold_project.suffix.lower() != ".qpproj":
                raise ValueError("Choose an existing QuPath project.qpproj file for threshold optimization.")
            projects = [threshold_project]
            output_directory = threshold_project.parent / "brain_cell_counter_threshold_results"
        else:
            projects = [current_project]
            output_directory = current_project.parent / "brain_cell_counter_results"

        targets: list[dict[str, str]] = []
        for project_path in projects:
            images = self._read_project_entries(project_path)
            if selected_only:
                selected_image = self.values["image"].get().strip()
                images = [selected_image] if selected_image in images else []
            for image_name in images:
                targets.append(
                    {
                        "project": str(project_path),
                        "image": image_name,
                        "section_id": f"{project_path.parent} :: {image_name}",
                    }
                )
        if not targets:
            raise ValueError("No image entries were selected for threshold optimization.")
        return targets, output_directory

    def _threshold_rules(self) -> dict[str, object]:
        total_limit = float(self.values["total_change_limit"].get())
        section_limit = float(self.values["section_change_limit"].get())
        stable_percent = float(self.values["stable_section_percent"].get())
        minimum_steps = int(self.values["minimum_stable_steps"].get())
        if total_limit <= 0 or section_limit <= 0:
            raise ValueError("Total and section change limits must be greater than zero.")
        if not 0 < stable_percent <= 100:
            raise ValueError("Stable sections must be greater than 0% and at most 100%.")
        if minimum_steps < 1:
            raise ValueError("Consecutive stable steps must be at least 1.")
        objective = self.values["threshold_objective"].get()
        if objective in THRESHOLD_PAIR_BY_TYPE:
            objective_key = "maximize_pair_overlap"
            pair_keys: tuple[str, str] | None = THRESHOLD_PAIR_BY_TYPE[objective]
        elif objective == INDIVIDUAL_THRESHOLD_TYPE:
            objective_key = "independent_plateaus"
            pair_keys = None
        else:
            raise ValueError("Choose a valid threshold optimization objective.")
        return {
            "total_change_limit": total_limit,
            "section_change_limit": section_limit,
            "required_stable_fraction": stable_percent / 100.0,
            "minimum_consecutive_steps": minimum_steps,
            "objective": objective_key,
            "pair_keys": pair_keys,
            "automatic_background_guard": bool(
                self.values["automatic_background_guard"].get()
            ),
        }

    def _start_threshold_optimization(self) -> None:
        qupath = Path(self.values["qupath"].get().strip())
        engine = Path(__file__).resolve().parent.parent / "Brain_Cell_Counter_QuPath_Engine_v1.0.groovy"
        if not qupath.is_file() or not engine.is_file():
            messagebox.showerror("Missing input", "Check the QuPath executable and analysis engine file.")
            return
        try:
            arguments = self._parameters("threshold-profile")
            targets, output_directory = self._threshold_targets()
            rules = self._threshold_rules()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            messagebox.showerror("Global threshold", str(exc))
            return

        scope = self.values["threshold_scope"].get()
        objective_label = self.values["threshold_objective"].get()
        warning = ""
        if scope == "Selected entry only — test":
            warning = "\n\nThis is a one-section test and must not be treated as a locked global batch threshold."
        if not messagebox.askyesno(
            "Find global threshold",
            f"Scan existing intensity measurements in {len(targets)} section(s)?\n"
            "Cellpose will not run again. Every selected section must already contain detections."
            f"\n\nObjective: {objective_label}."
            f"{warning}",
        ):
            return

        self._save_channel_names(show_confirmation=False)
        self._show_log_window()
        self._append_log("=" * 72)
        self._append_log(
            f"Global threshold scan: {len(targets)} section(s); scope: {scope}; objective: {objective_label}"
        )
        self.threshold_profiles = []
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._run_threshold_batch,
            args=(qupath, engine, arguments, targets, output_directory, rules, scope),
            daemon=True,
        )
        self.worker.start()

    def _run_threshold_batch(
        self,
        qupath: Path,
        engine: Path,
        arguments: list[str],
        targets: list[dict[str, str]],
        output_directory: Path,
        rules: dict[str, object],
        scope: str,
    ) -> None:
        profiles: list[dict] = []
        try:
            for target_index, target in enumerate(targets):
                self.events.put(("log", f"[{target_index + 1}/{len(targets)}] {target['image']}"))
                command = [
                    str(qupath),
                    "-q",
                    "script",
                    "-p",
                    target["project"],
                    "-i",
                    target["image"],
                    str(engine),
                ]
                for argument in arguments:
                    command.extend(["--args", argument])

                output_tail: list[str] = []
                entry_profile: dict | None = None
                entry_error: str | None = None
                self.process = subprocess.Popen(
                    command,
                    cwd=str(Path(__file__).resolve().parent.parent),
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
                        output_tail.append(line)
                        output_tail = output_tail[-20:]
                    if not line.startswith("BCC_EVENT "):
                        continue
                    event = self._decode_bcc_event(line)
                    if event is None:
                        self.events.put((
                            "log",
                            f"[{target_index + 1}/{len(targets)}] WARNING: Ignored a malformed BCC_EVENT line.",
                        ))
                        continue
                    kind = event.get("kind")
                    payload = event.get("payload")
                    if kind == "threshold_profile" and isinstance(payload, dict):
                        entry_profile = payload
                    elif kind == "error":
                        entry_error = str(payload)
                    elif kind == "progress" and isinstance(payload, dict):
                        entry_percent = float(payload.get("percent", 0))
                        overall = (target_index + entry_percent / 100.0) / len(targets) * 100.0
                        self.events.put(
                            (
                                "progress",
                                {
                                    "percent": overall,
                                    "stage": f"Threshold scan {target_index + 1}/{len(targets)} — {payload.get('stage', 'processing')}",
                                },
                            )
                        )
                return_code = self.process.wait()
                if return_code != 0 or entry_error or entry_profile is None:
                    details = entry_error or "\n".join(output_tail[-8:])
                    raise RuntimeError(
                        f"Threshold scan failed for {target['image']} (QuPath code {return_code}).\n{details}"
                    )
                entry_profile["section_id"] = target["section_id"]
                entry_profile["project"] = target["project"]
                profiles.append(entry_profile)

            result = calculate_global_thresholds(profiles, **rules)
            result["scope"] = scope
            result["reports"] = write_threshold_reports(result, output_directory)
            self.events.put(("threshold_optimization", result))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.process = None

    def _choose_batch_images(self) -> None:
        image_names = list(self.project_entries)
        if not image_names:
            messagebox.showerror("Analyze multiple entries", "Load a QuPath project containing image entries first.")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Select image entries for batch analysis")
        dialog.geometry("620x520")
        dialog.minsize(480, 360)
        dialog.transient(self)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                "Select one or more image entries. Processing order is always top to bottom, following the "
                "QuPath project-entry order. Each entry must finish before the next entry starts."
            ),
            wraplength=580,
        ).pack(anchor="w", pady=(0, 8))
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True)
        image_list = tk.Listbox(list_frame, selectmode="extended", exportselection=False)
        image_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=image_list.yview)
        scrollbar.pack(side="right", fill="y")
        image_list.configure(yscrollcommand=scrollbar.set)
        for image_name in image_names:
            image_list.insert("end", image_name)
        current_image = self.values["image"].get()
        if current_image in image_names:
            image_list.selection_set(image_names.index(current_image))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(10, 0))
        ttk.Button(controls, text="Select all", command=lambda: image_list.selection_set(0, "end")).pack(side="left")
        ttk.Button(controls, text="Clear", command=lambda: image_list.selection_clear(0, "end")).pack(
            side="left", padx=6
        )

        def run_selected() -> None:
            selected_indexes = sorted(int(index) for index in image_list.curselection())
            selected = [image_names[index] for index in selected_indexes]
            if not selected:
                messagebox.showerror("Analyze multiple entries", "Select at least one image entry.", parent=dialog)
                return
            dialog.grab_release()
            dialog.destroy()
            self._start_batch_analysis(selected)

        ttk.Button(controls, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(controls, text="ANALYZE SELECTED & SAVE", command=run_selected).pack(
            side="right", padx=(0, 8)
        )

    def _start_validation_batch(self) -> None:
        image_names = list(self.project_entries)
        qupath = Path(self.values["qupath"].get().strip())
        project = Path(self.values["project"].get().strip())
        engine = Path(__file__).resolve().parent.parent / "Brain_Cell_Counter_QuPath_Engine_v1.0.groovy"
        if not image_names:
            messagebox.showerror("Validate all entries", "Load a QuPath project containing image entries first.")
            return
        if not qupath.is_file() or not project.is_file() or not engine.is_file():
            messagebox.showerror("Missing input", "Check the QuPath executable, project, and analysis engine file.")
            return
        try:
            arguments = self._parameters("validate")
        except ValueError as exc:
            messagebox.showerror("Invalid parameters", str(exc))
            return
        if not messagebox.askyesno(
            "Validate all project image entries",
            f"Validate all {len(image_names)} image entries without saving project changes?\n\n"
            "Inaccessible entries will be reported and the remaining entries will continue.",
        ):
            return
        self._save_channel_names(show_confirmation=False)
        self._show_log_window()
        self._append_log("=" * 72)
        self._append_log(f"Validate-all-entries batch started: {len(image_names)} project image entries")
        self.batch_cancel_requested = False
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._run_entry_batch,
            args=(qupath, project, engine, arguments, image_names, "validate", False),
            daemon=True,
        )
        self.worker.start()

    @staticmethod
    def _analysis_checkpoint_path(project: Path) -> Path:
        return project.parent / "brain_cell_counter_results" / f"batch_checkpoint_v{OUTPUT_SCHEMA_VERSION}.json"

    @staticmethod
    def _analysis_signature(project: Path, arguments: list[str]) -> str:
        payload = json.dumps(
            {"project": str(project.resolve()), "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_analysis_checkpoint(path: Path, signature: str) -> set[str]:
        if not path.is_file():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("signature") != signature:
                return set()
            return {str(name) for name in data.get("completed", [])}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return set()

    @staticmethod
    def _decode_bcc_event(line: str) -> dict | None:
        """Decode one engine event without allowing damaged log output to stop a run."""
        prefix = "BCC_EVENT "
        if not line.startswith(prefix):
            return None
        try:
            event = json.loads(line[len(prefix) :])
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return event if isinstance(event, dict) else None

    @staticmethod
    def _missing_batch_payload_message(
        mode: str,
        validation_result: dict | None,
        entry_result: dict | None,
    ) -> str | None:
        if mode == "validate" and validation_result is None:
            return "QuPath exited successfully but did not emit a validation result."
        if mode != "validate" and entry_result is None:
            operation = "ABBA import" if mode == "apply-atlas" else "analysis"
            return f"QuPath exited successfully but did not emit an {operation} result."
        return None

    @staticmethod
    def _write_analysis_checkpoint(
        path: Path,
        project: Path,
        signature: str,
        completed: set[str],
        failed: list[dict[str, str]],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": APP_VERSION,
                    "project": str(project.resolve()),
                    "signature": signature,
                    "updated": datetime.now().isoformat(timespec="seconds"),
                    "completed": sorted(completed),
                    "failed": failed,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _safe_write_analysis_checkpoint(
        self,
        path: Path,
        project: Path,
        signature: str,
        completed: set[str],
        failed: list[dict[str, str]],
    ) -> bool:
        """Persist resume state without sacrificing completed image analysis."""
        try:
            self._write_analysis_checkpoint(path, project, signature, completed, failed)
            return True
        except (OSError, ValueError, TypeError) as exc:
            self.events.put((
                "log",
                "WARNING: Analysis succeeded, but the resume checkpoint could not be updated: "
                + str(exc),
            ))
            return False

    def _start_batch_analysis(self, image_names: list[str]) -> None:
        qupath = Path(self.values["qupath"].get().strip())
        project = Path(self.values["project"].get().strip())
        engine = Path(__file__).resolve().parent.parent / "Brain_Cell_Counter_QuPath_Engine_v1.0.groovy"
        if not qupath.is_file() or not project.is_file() or not engine.is_file():
            messagebox.showerror("Missing input", "Check the QuPath executable, project, and analysis engine file.")
            return
        try:
            arguments = self._parameters("analyze")
        except ValueError as exc:
            messagebox.showerror("Invalid parameters", str(exc))
            return
        try:
            _, source_scan = self._scan_project_sources(project)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            messagebox.showerror("Project preflight", f"Could not inspect project image links:\n{exc}")
            return
        selected_names = set(image_names)
        missing_selected = [
            str(item["image_name"])
            for item in source_scan
            if str(item["image_name"]) in selected_names and not item["exists"]
        ]
        if missing_selected:
            messagebox.showerror(
                "Project preflight required",
                "The following selected entries have missing source images:\n\n- "
                + "\n- ".join(missing_selected)
                + "\n\nRun PREFLIGHT and repair the links before analysis.",
            )
            return

        checkpoint_path = self._analysis_checkpoint_path(project)
        checkpoint_signature = self._analysis_signature(project, arguments)
        checkpoint_completed = self._read_analysis_checkpoint(checkpoint_path, checkpoint_signature)
        resumable = [name for name in image_names if name in checkpoint_completed]
        skipped: list[str] = []
        if resumable:
            resume_choice = messagebox.askyesnocancel(
                "Resume batch",
                f"A matching checkpoint contains {len(resumable)} completed selected entry/entries.\n\n"
                "Yes: skip completed entries and resume.\n"
                "No: rerun every selected entry.\n"
                "Cancel: return without starting.",
            )
            if resume_choice is None:
                return
            if resume_choice:
                skipped = resumable
                image_names = [name for name in image_names if name not in checkpoint_completed]
            else:
                checkpoint_completed.difference_update(image_names)
        if not image_names:
            messagebox.showinfo(
                "Resume batch",
                "All selected entries are already complete for the current analysis parameters.",
            )
            return
        if not messagebox.askyesno(
            "Analyze multiple image entries",
            f"Analyze and save {len(image_names)} selected image entries?\n\n"
            "Entries will run sequentially. Failed entries will be logged and the remaining entries will continue. "
            "Close the QuPath desktop window before continuing.",
        ):
            return

        self._save_channel_names(show_confirmation=False)
        self._show_log_window()
        self._append_log("=" * 72)
        self._append_log(f"Batch analysis started: {len(image_names)} image entries")
        if skipped:
            self._append_log(f"Resume checkpoint: skipped {len(skipped)} completed entry/entries.")
            for image_name in skipped:
                self._append_log(f"  SKIPPED COMPLETE: {image_name}")
        self._append_log("Processing queue (top to bottom):")
        for queue_index, image_name in enumerate(image_names, start=1):
            self._append_log(f"  {queue_index}. {image_name}")
        self.batch_cancel_requested = False
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._run_entry_batch,
            args=(
                qupath,
                project,
                engine,
                arguments,
                image_names,
                "analyze",
                True,
                checkpoint_path,
                checkpoint_signature,
                checkpoint_completed,
                skipped,
            ),
            daemon=True,
        )
        self.worker.start()

    def _run_entry_batch(
        self,
        qupath: Path,
        project: Path,
        engine: Path,
        arguments: list[str],
        image_names: list[str],
        mode: str,
        save_entries: bool,
        checkpoint_path: Path | None = None,
        checkpoint_signature: str | None = None,
        checkpoint_completed: set[str] | None = None,
        resume_skipped: list[str] | None = None,
    ) -> None:
        completed: list[str] = []
        failed: list[dict[str, str]] = []
        not_ready: list[dict[str, object]] = []
        persisted_completed = set(checkpoint_completed or ())
        resume_skipped = list(resume_skipped or [])
        try:
            for image_index, image_name in enumerate(image_names):
                if self.batch_cancel_requested:
                    break
                operation_label = {
                    "validate": "VALIDATE",
                    "apply-atlas": "IMPORT ABBA & SAVE",
                }.get(mode, "ANALYZE & SAVE")
                self.events.put((
                    "log",
                    f"[{image_index + 1}/{len(image_names)}] Starting {operation_label}: {image_name}",
                ))
                command = [str(qupath), "-q", "script", "-p", str(project), "-i", image_name]
                if save_entries:
                    command.append("--save")
                command.append(str(engine))
                for argument in arguments:
                    command.extend(["--args", argument])
                output_tail: list[str] = []
                entry_error: str | None = None
                entry_result: dict | None = None
                validation_result: dict | None = None
                last_progress_stage: str | None = None
                last_cellpose_log_bucket = -1
                imported_mask_tiles = 0
                self.process = subprocess.Popen(
                    command,
                    cwd=str(Path(__file__).resolve().parent.parent),
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
                        output_tail.append(line)
                        output_tail = output_tail[-25:]
                    if not line.startswith("BCC_EVENT "):
                        if line and not line.startswith("WARNING: Unknown module:"):
                            cellpose_percent = re.search(r"(\d{1,3})%\|", line)
                            if cellpose_percent:
                                raw_percent = min(100, int(cellpose_percent.group(1)))
                                bucket = raw_percent // 10
                                if bucket > last_cellpose_log_bucket:
                                    last_cellpose_log_bucket = bucket
                                    self.events.put((
                                        "log",
                                        f"[{image_index + 1}/{len(image_names)}] Cellpose CUDA progress: {raw_percent}%",
                                    ))
                            elif "Reading Temp_" in line:
                                imported_mask_tiles += 1
                                if imported_mask_tiles == 1 or imported_mask_tiles % 10 == 0:
                                    self.events.put((
                                        "log",
                                        f"[{image_index + 1}/{len(image_names)}] Imported {imported_mask_tiles} Cellpose mask tiles",
                                    ))
                            elif any(marker in line for marker in (
                                "Saving images for",
                                "Executing command:",
                                "using GPU (CUDA)",
                                ">>>> loading model",
                                ">>>> running cellpose",
                                ">>>> completed in",
                                "[ERROR]",
                                "Exception",
                            )):
                                self.events.put((
                                    "log",
                                    f"[{image_index + 1}/{len(image_names)}] {line}",
                                ))
                        continue
                    event = self._decode_bcc_event(line)
                    if event is None:
                        self.events.put((
                            "log",
                            f"[{image_index + 1}/{len(image_names)}] WARNING: Ignored a malformed BCC_EVENT line.",
                        ))
                        continue
                    kind = event.get("kind")
                    payload = event.get("payload")
                    if kind == "progress" and isinstance(payload, dict):
                        entry_percent = float(payload.get("percent", 0))
                        stage = str(payload.get("stage", "processing"))
                        overall = (image_index + entry_percent / 100.0) / len(image_names) * 100.0
                        self.events.put(("progress", {
                            "percent": overall,
                            "stage": f"Image {image_index + 1}/{len(image_names)} — {stage}",
                        }))
                        if stage != last_progress_stage:
                            last_progress_stage = stage
                            self.events.put((
                                "log",
                                f"[{image_index + 1}/{len(image_names)}] {stage} ({entry_percent:.0f}%)",
                            ))
                    elif kind == "error":
                        entry_error = str(payload)
                    elif kind == "validation" and isinstance(payload, dict):
                        validation_result = payload
                    elif kind == "result" and isinstance(payload, dict):
                        entry_result = payload
                return_code = self.process.wait()
                # The next loop iteration cannot start until this QuPath process
                # has exited, guaranteeing strict top-to-bottom sequential order.
                if self.batch_cancel_requested:
                    break
                missing_payload = self._missing_batch_payload_message(
                    mode,
                    validation_result,
                    entry_result,
                )
                if return_code != 0 or entry_error or missing_payload:
                    details = entry_error or missing_payload or "\n".join(output_tail[-8:])
                    failed.append({"image": image_name, "error": details})
                    self.events.put(("log", f"[{image_index + 1}/{len(image_names)}] FAILED: {image_name}\n{details}"))
                    if checkpoint_path is not None and checkpoint_signature is not None:
                        self._safe_write_analysis_checkpoint(
                            checkpoint_path,
                            project,
                            checkpoint_signature,
                            persisted_completed,
                            failed,
                        )
                    continue
                completed.append(image_name)
                if checkpoint_path is not None and checkpoint_signature is not None:
                    persisted_completed.add(image_name)
                    self._safe_write_analysis_checkpoint(
                        checkpoint_path,
                        project,
                        checkpoint_signature,
                        persisted_completed,
                        failed,
                    )
                if mode == "validate" and validation_result is not None:
                    if not bool(validation_result.get("ready")):
                        not_ready.append({"image": image_name, "validation": validation_result})
                        self.events.put(("log", f"[{image_index + 1}/{len(image_names)}] NOT READY: {image_name}"))
                    else:
                        self.events.put(("log", f"[{image_index + 1}/{len(image_names)}] READY: {image_name}"))
                if entry_result:
                    self.events.put(("result", entry_result))
                if mode != "validate":
                    self.events.put(("log", f"[{image_index + 1}/{len(image_names)}] Completed {image_name}"))
        except Exception as exc:
            failed.append({"image": "Batch controller", "error": str(exc)})
        finally:
            self.process = None
            self.events.put(("batch_done", {
                "requested": len(image_names) + len(resume_skipped),
                "completed": completed,
                "skipped": resume_skipped,
                "failed": failed,
                "not_ready": not_ready,
                "cancelled": self.batch_cancel_requested,
                "mode": mode,
            }))

    def _start(self, mode: str) -> None:
        qupath = Path(self.values["qupath"].get().strip())
        project = Path(self.values["project"].get().strip())
        image_name = self.values["image"].get().strip()
        engine = Path(__file__).resolve().parent.parent / "Brain_Cell_Counter_QuPath_Engine_v1.0.groovy"
        if not qupath.is_file() or not project.is_file() or not image_name or not engine.is_file():
            messagebox.showerror("Missing input", "Check the QuPath executable, project, image entry and analysis engine file.")
            return
        try:
            arguments = self._parameters(mode)
        except ValueError as exc:
            messagebox.showerror("Invalid parameters", str(exc))
            return
        self._save_channel_names(show_confirmation=False)
        if mode == "analyze" and not messagebox.askyesno(
            "Analyze and save",
            "This will run Cellpose if needed, reclassify detections, write regional CSV and save the QuPath entry. Continue?",
        ):
            return
        if mode == "cellpose-calibrate" and not messagebox.askyesno(
            "Auto-suggest Cellpose parameters",
            "This will test 15 Cellprob/Flow combinations on three representative 640×640 crops from the selected image.\n\n"
            "It can take several minutes, does not alter the saved QuPath entry, and produces a heuristic suggestion that still requires visual review. Continue?",
        ):
            return

        command = [str(qupath), "-q", "script", "-p", str(project), "-i", image_name]
        if mode == "analyze":
            command.append("--save")
        command.append(str(engine))
        for argument in arguments:
            command.extend(["--args", argument])

        self._show_log_window()
        self._append_log("=" * 72)
        self._append_log(f"Mode: {mode}; image: {image_name}")
        self.expected_cellpose_tiles = 0
        self.read_cellpose_tiles = 0
        self._set_running(True)
        self.worker = threading.Thread(target=self._run_command, args=(command, mode), daemon=True)
        self.worker.start()

    def _run_command(self, command: list[str], mode: str) -> None:
        terminal_event = False
        output_tail: list[str] = []
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parent.parent),
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
                    output_tail.append(line)
                    output_tail = output_tail[-30:]
                if line.startswith("BCC_EVENT "):
                    event = self._decode_bcc_event(line)
                    if event is None:
                        self.events.put(("log", "WARNING: Ignored a malformed BCC_EVENT line."))
                        continue
                    kind = event.get("kind")
                    payload = event.get("payload")
                    self.events.put((kind, payload))
                    terminal_event = terminal_event or kind in {"done", "error"}
                elif line and not line.startswith("WARNING: Unknown module:"):
                    self._infer_progress_from_log(line)
                    self.events.put(("log", line))
            return_code = self.process.wait()
            if return_code != 0 and not terminal_event:
                useful_tail = [
                    line for line in output_tail
                    if not line.startswith("WARNING: Unknown module:")
                ]
                details = "\n".join(useful_tail[-12:])
                message = f"QuPath exited with code {return_code}."
                if details:
                    message += "\n\nLast QuPath messages:\n" + details
                self.events.put(("error", message))
            elif not terminal_event:
                self.events.put(("done", {"mode": mode}))
        except Exception as exc:
            self.events.put(("error", f"Could not launch QuPath: {exc}"))
        finally:
            self.process = None

    def _cancel(self) -> None:
        self.batch_cancel_requested = True
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self._set_progress(self.progress_value.get(), "Cancelling…")
            self._append_log("QuPath process termination requested.")

    def _infer_progress_from_log(self, line: str) -> None:
        """Translate QuPath/Cellpose console output into real tile progress."""
        tile_count = re.search(r"Saving images for (\d+) tiles", line)
        if tile_count:
            self.expected_cellpose_tiles = max(1, int(tile_count.group(1)))
            self.events.put(("progress", {"percent": 18, "stage": f"Preparing {self.expected_cellpose_tiles} Cellpose tiles"}))
            return

        cellpose_percent = re.search(r"(\d{1,3})%\|", line)
        if cellpose_percent:
            raw_percent = min(100, int(cellpose_percent.group(1)))
            mapped_percent = 22 + raw_percent * 0.48
            self.events.put(("progress", {"percent": mapped_percent, "stage": f"Cellpose GPU — {raw_percent}% of tiles"}))
            return

        if "using GPU (CUDA)" in line:
            self.events.put(("progress", {"percent": 22, "stage": "Cellpose is using CUDA"}))
        elif ">>>> loading model" in line:
            self.events.put(("progress", {"percent": 24, "stage": "Loading Cellpose-SAM model"}))
        elif ">>>> completed in" in line:
            self.events.put(("progress", {"percent": 70, "stage": "Cellpose segmentation complete"}))
        elif "Reading Temp_" in line:
            self.read_cellpose_tiles += 1
            if self.expected_cellpose_tiles:
                fraction = min(1.0, self.read_cellpose_tiles / self.expected_cellpose_tiles)
                percent = 70 + 6 * fraction
                stage = f"Importing masks — {self.read_cellpose_tiles}/{self.expected_cellpose_tiles} tiles"
            else:
                percent = 73
                stage = "Importing Cellpose masks"
            self.events.put(("progress", {"percent": percent, "stage": stage}))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "progress":
                    if isinstance(payload, dict):
                        self._set_progress(payload.get("percent", 0), str(payload.get("stage", "Processing")))
                elif kind == "validation":
                    self._append_log("VALIDATION: " + json.dumps(payload, indent=2, ensure_ascii=False))
                elif kind == "result":
                    self._append_log("RESULT: " + json.dumps(payload, indent=2, ensure_ascii=False))
                    if isinstance(payload, dict) and payload.get("csv"):
                        self.last_output_folder = Path(payload["csv"]).parent
                        self.results_button.configure(state="normal")
                        self.results_preview.refresh()
                elif kind == "threshold_optimization":
                    self._set_progress(100, "Global threshold optimization complete")
                    self._set_running(False)
                    channels = payload.get("channels", []) if isinstance(payload, dict) else []
                    reports = payload.get("reports", {}) if isinstance(payload, dict) else {}
                    if reports.get("csv"):
                        self.last_output_folder = Path(reports["csv"]).parent
                        self.results_button.configure(state="normal")
                    summary_lines = [
                        f"{channel.get('name')}: "
                        + (
                            f"{float(channel['selected_threshold']):g}"
                            if channel.get("selected_threshold") is not None
                            else "No stable plateau"
                        )
                        for channel in channels
                    ]
                    double_positive = payload.get("double_positive_optimization") if isinstance(payload, dict) else None
                    if isinstance(double_positive, dict):
                        summary_lines.append(
                            f"{double_positive['first_channel_name']} + "
                            f"{double_positive['second_channel_name']} double-positive objects: "
                            f"{int(double_positive['total_double_positive'])} "
                            f"at thresholds {float(double_positive['selected_first_threshold']):g} / "
                            f"{float(double_positive['selected_second_threshold']):g}"
                        )
                    guard_details = []
                    guard_warnings = []
                    for channel in channels:
                        separation = channel.get("automatic_signal_separation")
                        if isinstance(separation, dict):
                            guard_details.append(
                                f"{channel.get('name')} automatic split: "
                                f"{float(separation['threshold']):g}; "
                                f"positive fraction={float(separation['positive_fraction_pct']):.1f}%; "
                                f"separability={float(separation['separability_score']):.2f}"
                            )
                        if channel.get("selection_source") == "automatic_signal_separation_fallback":
                            guard_warnings.append(
                                f"{channel.get('name')}: no stable plateau remained after background rejection; "
                                "the automatic separation threshold was used as a fallback"
                            )
                        if channel.get("automatic_guard_warning"):
                            guard_warnings.append(
                                f"{channel.get('name')}: {channel.get('automatic_guard_warning')}"
                            )
                    for detail in guard_details:
                        self._append_log("AUTO BACKGROUND GUARD: " + detail)
                    self._append_log("GLOBAL THRESHOLD RESULT: " + "; ".join(summary_lines))
                    if reports:
                        self._append_log("Reports: " + json.dumps(reports, ensure_ascii=False))

                    missing = [channel.get("name") for channel in channels if channel.get("selected_threshold") is None]
                    boundary = [
                        channel.get("name")
                        for channel in channels
                        if channel.get("range_ends_inside_plateau")
                    ]
                    if missing:
                        messagebox.showwarning(
                            "Global threshold",
                            "No qualifying stable plateau was found for: "
                            + ", ".join(str(name) for name in missing)
                            + ".\n\nReview the HTML/CSV report and adjust the tested range or step size.",
                        )
                        continue
                    if payload.get("scope") == "Selected entry only — test":
                        messagebox.showinfo(
                            "Threshold test complete",
                            "One-section test completed. Recommendations were not applied because a global threshold "
                            "must be calculated across the complete imaging batch.\n\n"
                            + "\n".join(summary_lines),
                        )
                        continue

                    caution = ""
                    if boundary:
                        caution = (
                            "\n\nCaution: the stable plateau reaches the end of the tested range for "
                            + ", ".join(str(name) for name in boundary)
                            + ". Consider extending that range before final locking."
                        )
                    if guard_warnings:
                        caution += "\n\nAutomatic guard warnings:\n- " + "\n- ".join(guard_warnings)
                    apply_result = messagebox.askyesno(
                        "Apply global thresholds",
                        "Optimization completed across "
                        f"{payload.get('section_count')} section(s):\n\n"
                        + "\n".join(summary_lines)
                        + caution
                        + "\n\nApply these values and lock all three channel thresholds?",
                    )
                    if apply_result:
                        channel_by_key = {channel["key"]: channel for channel in channels}
                        self.values["neun_threshold"].set(
                            f"{float(channel_by_key['channel1']['selected_threshold']):g}"
                        )
                        self.values["cfos_threshold"].set(
                            f"{float(channel_by_key['channel2']['selected_threshold']):g}"
                        )
                        self.values["dapi_threshold"].set(
                            f"{float(channel_by_key['channel3']['selected_threshold']):g}"
                        )
                        self.values["require_dapi"].set(True)
                        self.values["threshold_locked"].set(True)
                        self._update_threshold_lock_state()
                        self._save_channel_names(show_confirmation=False)
                        self._append_log("Optimized global thresholds applied and locked.")
                elif kind == "done":
                    self._set_progress(100, "Completed")
                    self._set_running(False)
                    self._append_log("Completed successfully.")
                    if isinstance(payload, dict) and payload.get("mode") == "cellpose-calibrate":
                        suggested_cellprob = float(payload["suggestedCellprob"])
                        suggested_flow = float(payload["suggestedFlow"])
                        stability_score = float(payload["stabilityScore"])
                        self._append_log(
                            "CELLPOSE SUGGESTION: "
                            f"Cellprob={suggested_cellprob:g}; Flow={suggested_flow:g}; "
                            f"stability score={stability_score:.2f}%"
                        )
                        apply_suggestion = messagebox.askyesno(
                            "Cellpose parameter suggestion",
                            f"Suggested Cellprob threshold: {suggested_cellprob:g}\n"
                            f"Suggested Flow threshold: {suggested_flow:g}\n"
                            f"Local stability score: {stability_score:.2f}%\n\n"
                            "This is a heuristic result from three representative crops. "
                            "Inspect masks visually before using it for the full imaging batch.\n\n"
                            "Apply these suggested values?",
                        )
                        if apply_suggestion:
                            self.values["cellprob"].set(f"{suggested_cellprob:g}")
                            self.values["flow"].set(f"{suggested_flow:g}")
                            self._save_channel_names(show_confirmation=False)
                            self._append_log("Suggested Cellprob and Flow values applied.")
                    elif isinstance(payload, dict) and "ready" in payload:
                        messagebox.showinfo(
                            "Validation complete",
                            "Ready for analysis." if payload["ready"] else "Not ready. Review the validation log.",
                        )
                    else:
                        messagebox.showinfo("Brain Cell Counter", "QuPath processing completed.")
                elif kind == "batch_done":
                    self._set_running(False)
                    completed = payload.get("completed", []) if isinstance(payload, dict) else []
                    skipped = payload.get("skipped", []) if isinstance(payload, dict) else []
                    failed = payload.get("failed", []) if isinstance(payload, dict) else []
                    not_ready = payload.get("not_ready", []) if isinstance(payload, dict) else []
                    batch_mode = str(payload.get("mode", "analyze")) if isinstance(payload, dict) else "analyze"
                    cancelled = bool(payload.get("cancelled", False)) if isinstance(payload, dict) else False
                    requested = int(payload.get("requested", len(completed) + len(failed))) if isinstance(payload, dict) else 0
                    if cancelled:
                        self._set_progress(self.progress_value.get(), "Batch cancelled")
                    else:
                        completed_stage = (
                            "Validate-all complete" if batch_mode == "validate" else
                            "ABBA import complete" if batch_mode == "apply-atlas" else
                            "Batch analysis complete"
                        )
                        self._set_progress(100, completed_stage)
                    if batch_mode == "validate":
                        ready_count = len(completed) - len(not_ready)
                        summary = (
                            f"Validate-all finished: {ready_count}/{requested} ready; "
                            f"{len(not_ready)} not ready; {len(failed)} inaccessible/failed; "
                            f"cancelled={'yes' if cancelled else 'no'}."
                        )
                    elif batch_mode == "apply-atlas":
                        summary = (
                            f"ABBA import finished: {len(completed)}/{requested} imported; "
                            f"{len(failed)} failed; cancelled={'yes' if cancelled else 'no'}."
                        )
                    else:
                        summary = (
                            f"Batch finished: {len(completed)} newly completed; "
                            f"{len(skipped)} previously completed/skipped; "
                            f"{len(failed)} failed; cancelled={'yes' if cancelled else 'no'}."
                        )
                    self._append_log(summary)
                    if failed or not_ready:
                        problem_lines = []
                        if failed:
                            problem_lines.append(
                                "Inaccessible/failed entries:\n"
                                + "\n".join(f"- {item.get('image')}" for item in failed)
                            )
                        if not_ready:
                            problem_lines.append(
                                "Not-ready entries:\n"
                                + "\n".join(f"- {item.get('image')}" for item in not_ready)
                            )
                        result_title = (
                            "Validate-all results" if batch_mode == "validate" else
                            "ABBA import finished with errors" if batch_mode == "apply-atlas" else
                            "Batch analysis finished with errors"
                        )
                        messagebox.showwarning(
                            result_title,
                            summary + "\n\n" + "\n\n".join(problem_lines) + "\n\nSee the run log for details.",
                        )
                    else:
                        complete_title = (
                            "Validate-all complete" if batch_mode == "validate" else
                            "ABBA import complete" if batch_mode == "apply-atlas" else
                            "Batch analysis complete"
                        )
                        messagebox.showinfo(
                            complete_title,
                            summary,
                        )
                elif kind == "error":
                    self._set_progress(self.progress_value.get(), "Failed")
                    self._set_running(False)
                    self._append_log("ERROR: " + str(payload))
                    messagebox.showerror("QuPath analysis failed", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        if running:
            for attribute in ("advanced_cellpose_window", "advanced_threshold_window"):
                window = getattr(self, attribute)
                if window is not None and window.winfo_exists():
                    window.destroy()
                setattr(self, attribute, None)
        state = "disabled" if running else "normal"
        self.validate_all_button.configure(state=state)
        self.batch_analyze_button.configure(state=state)
        self.import_abba_button.configure(state=state)
        self.calibration_button.configure(state=state)
        self.calibrate_button.configure(state=state)
        self.cellpose_advanced_button.configure(state=state)
        self.threshold_button.configure(state=state)
        self.threshold_advanced_button.configure(state=state)
        self.results_preview.set_busy(running)
        self.cancel_button.configure(state="normal" if running else "disabled")
        if running:
            self._set_progress(2, "Starting QuPath", allow_decrease=True)

    def _set_progress(self, percent: float, stage: str, allow_decrease: bool = False) -> None:
        try:
            value = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return
        if not allow_decrease:
            value = max(value, self.progress_value.get())
        self.progress_value.set(value)
        self.progress_text.set(f"{stage} — {value:.0f}%")
        stage_lower = stage.casefold()
        if "fail" in stage_lower or "error" in stage_lower:
            badge_text, badge_style = "ERROR", "StatusError.TLabel"
        elif "cancel" in stage_lower or "stop" in stage_lower:
            badge_text, badge_style = "CANCELLED", "StatusCancelled.TLabel"
        elif value >= 100.0 or "complete" in stage_lower or "finished" in stage_lower:
            badge_text, badge_style = "COMPLETE", "StatusComplete.TLabel"
        elif value <= 0.0 and "ready" in stage_lower:
            badge_text, badge_style = "READY", "StatusReady.TLabel"
        else:
            badge_text, badge_style = "RUNNING", "StatusRunning.TLabel"
        self.run_state_text.set(badge_text)
        self.run_state_badge.configure(style=badge_style)

    def _append_log(self, text: str) -> None:
        if self.log_text is None or not self.log_text.winfo_exists():
            self._build_log_window()
        assert self.log_text is not None
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _open_results(self) -> None:
        if self.last_output_folder and self.last_output_folder.exists():
            os.startfile(self.last_output_folder)
        else:
            messagebox.showinfo("Results", "No results folder has been created yet.")

    def _open_result_in_qupath(self, project_path: Path, image_name: str) -> None:
        """Open the exact saved result entry in the normal QuPath desktop viewer."""
        if self.is_running:
            messagebox.showwarning(
                "Open in QuPath",
                "Wait for VALIDATE, ANALYZE, IMPORT ABBA, or calibration to finish before opening the project in QuPath.",
            )
            return
        configured_path = Path(self.values["qupath"].get().strip())
        launcher = qupath_gui_executable(configured_path)
        if not launcher.is_file():
            messagebox.showerror("Open in QuPath", "Choose an existing QuPath executable in Settings first.")
            return
        if not project_path.is_file():
            messagebox.showerror("Open in QuPath", "The selected QuPath project no longer exists.")
            return
        try:
            project_entries = self._read_project_entries(project_path)
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("Open in QuPath", f"Could not read the selected project:\n{exc}")
            return
        if image_name not in project_entries:
            messagebox.showerror(
                "Open in QuPath",
                f"The image entry is no longer present in this project:\n{image_name}",
            )
            return
        try:
            subprocess.Popen(
                [str(launcher), "-p", str(project_path), "-i", image_name],
                cwd=str(launcher.parent),
            )
        except OSError as exc:
            messagebox.showerror("Open in QuPath", f"Could not start QuPath:\n{exc}")
            return
        if Path(self.values["project"].get().strip()) == project_path:
            self.values["image"].set(image_name)


def main() -> None:
    app = HybridApp()
    app.mainloop()


if __name__ == "__main__":
    main()
