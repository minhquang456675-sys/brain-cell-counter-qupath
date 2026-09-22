"""Guard non-obvious QuPath engine scope and release-version invariants."""

from __future__ import annotations

import unittest
from pathlib import Path

from brain_cell_counter.version import APP_VERSION, OUTPUT_SCHEMA_VERSION


class EngineSourceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = (
            Path(__file__).resolve().parent.parent / "qupath_scripts" / "Brain_Cell_Counter_QuPath_Engine_v1.0.groovy"
        ).read_text(encoding="utf-8")

    def test_engine_reports_current_application_version(self) -> None:
        self.assertIn(f"version: '{APP_VERSION}'", self.engine)

    def test_engine_writes_current_output_schema(self) -> None:
        """Keep QuPath filenames aligned with the Python results/checkpoint schema."""
        self.assertIn(f"_regional_counts_v{OUTPUT_SCHEMA_VERSION}.csv", self.engine)
        self.assertIn(f"_atlas_overlay_v{OUTPUT_SCHEMA_VERSION}.png", self.engine)
        self.assertNotIn("_cell_overlay_points_v", self.engine)


if __name__ == "__main__":
    unittest.main()
