"""Launch only the calibration wizard without opening the classic main window.

The Qt interface starts this small process because Tk and Qt must not own the
same Windows event loop.  Users see the focused calibration workflow directly;
the legacy application shell is never displayed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk

from .calibration_wizard import CalibrationWizard


def main() -> None:
    """Open the dedicated human-reviewed calibration workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--qupath", required=True)
    parser.add_argument("--fiji", default="")
    arguments = parser.parse_args()

    root = tk.Tk()
    root.withdraw()
    wizard = CalibrationWizard(
        root,
        Path(arguments.qupath),
        Path(arguments.fiji) if arguments.fiji else Path(),
        Path(arguments.project),
        lambda _running: None,
    )
    wizard.transient(None)
    wizard.deiconify()
    wizard.lift()
    wizard.focus_force()
    root.wait_window(wizard)
    root.destroy()


if __name__ == "__main__":
    main()
