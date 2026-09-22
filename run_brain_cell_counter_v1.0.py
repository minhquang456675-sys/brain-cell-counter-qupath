"""Launch the modern interface, with the proven classic UI as a fallback."""

from __future__ import annotations

import sys


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
    selected_main()


if __name__ == "__main__":
    main()
