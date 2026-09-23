"""Launch only the calibration wizard without opening the classic main window.

The Qt interface starts this small process because Tk and Qt must not own the
same Windows event loop.  Users see the focused calibration workflow directly;
the legacy application shell is never displayed.
"""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from .calibration_wizard import CalibrationWizard


_MUTEX_NAME = "Local\\BrainCellCounterCalibrationWindow"


def _acquire_windows_mutex() -> int | None:
    """Allow only one standalone Calibration window on Windows."""
    if os.name != "nt":
        return 1
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return None
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_windows_mutex(handle: int | None) -> None:
    """Release the process-scoped Calibration mutex after the window closes."""
    if os.name == "nt" and handle:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def main() -> None:
    """Open the dedicated human-reviewed calibration workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--qupath", required=True)
    parser.add_argument("--fiji", default="")
    arguments = parser.parse_args()

    mutex = _acquire_windows_mutex()
    if mutex is None:
        duplicate_root = tk.Tk()
        duplicate_root.withdraw()
        messagebox.showinfo("Calibration", "The Calibration window is already open.", parent=duplicate_root)
        duplicate_root.destroy()
        return

    root = tk.Tk()
    root.withdraw()
    try:
        wizard = CalibrationWizard(
            root,
            Path(arguments.qupath),
            Path(arguments.fiji) if arguments.fiji else Path(),
            Path(arguments.project),
            lambda _running: None,
            standalone=True,
        )
        # An empty wm_transient value explicitly detaches the hidden owner.
        # Passing None only queries the existing owner in Tkinter.
        wizard.wm_transient("")
        wizard.update_idletasks()
        wizard.deiconify()
        wizard.state("normal")
        wizard.lift()
        wizard.attributes("-topmost", True)
        wizard.after(300, lambda: wizard.attributes("-topmost", False))
        wizard.focus_force()
        root.wait_window(wizard)
    finally:
        root.destroy()
        _release_windows_mutex(mutex)


if __name__ == "__main__":
    main()
