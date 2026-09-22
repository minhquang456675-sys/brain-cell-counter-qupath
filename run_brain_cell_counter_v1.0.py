"""Launch the modern interface, with the proven classic UI as a fallback."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def main() -> None:
    """Select Qt by default while retaining an explicit compatibility path."""
    if "--classic" in sys.argv:
        sys.argv.remove("--classic")
        from brain_cell_counter.hybrid_app import main as selected_main
    else:
        try:
            from brain_cell_counter.qt_app import main as selected_main
        except ImportError:
            # Older source checkouts can still start before the optional Qt
            # dependency is installed; the v2 installer always provides it.
            from brain_cell_counter.hybrid_app import main as selected_main
    try:
        selected_main()
    except Exception as exc:
        error_path = Path(__file__).resolve().parent / "brain_cell_counter_startup_error.log"
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"Brain Cell Counter could not start.\n\n{exc}\n\nDetails: {error_path}",
                    "Brain Cell Counter startup error",
                    0x10,
                )
            except (AttributeError, OSError):
                pass
        raise


if __name__ == "__main__":
    main()
