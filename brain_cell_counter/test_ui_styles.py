"""Regression checks for the shared visual behavior of the Qt interfaces."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class QtStyleTests(unittest.TestCase):
    """Keep native Windows scrollbar chrome out of both user-facing windows."""

    def test_main_and_calibration_use_compact_scrollbars(self) -> None:
        for filename in ("qt_app.py", "qt_calibration.py"):
            source = (ROOT / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertIn("QScrollBar::handle:vertical", source)
                self.assertIn("QScrollBar::handle:vertical:hover", source)
                self.assertIn("QScrollBar::add-line:vertical", source)
                self.assertIn("height: 0", source)


if __name__ == "__main__":
    unittest.main()
