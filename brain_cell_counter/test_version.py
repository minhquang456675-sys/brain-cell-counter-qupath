"""Verify that application, output-schema, and installer versions remain aligned."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from brain_cell_counter.version import APP_VERSION, OUTPUT_SCHEMA_VERSION


class VersionConsistencyTests(unittest.TestCase):
    def test_installer_manifest_matches_application_version(self) -> None:
        repository_root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (repository_root / "installer" / "installer_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], APP_VERSION)

    def test_output_schema_is_compatible_and_not_ahead_of_application(self) -> None:
        """UI-only releases may retain the preceding scientific output schema."""
        application_major_minor = tuple(int(part) for part in APP_VERSION.split(".")[:2])
        output_schema = tuple(int(part) for part in OUTPUT_SCHEMA_VERSION.split("."))
        self.assertEqual(output_schema[0], application_major_minor[0])
        self.assertLessEqual(output_schema, application_major_minor)


if __name__ == "__main__":
    unittest.main()
