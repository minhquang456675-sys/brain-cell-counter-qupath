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

    def test_output_schema_matches_major_minor_release(self) -> None:
        self.assertEqual(OUTPUT_SCHEMA_VERSION, ".".join(APP_VERSION.split(".")[:2]))


if __name__ == "__main__":
    unittest.main()
