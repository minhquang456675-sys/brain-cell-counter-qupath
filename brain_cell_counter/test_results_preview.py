"""Test discovery and bounded parsing of versioned regional CSV results."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain_cell_counter.results_preview import (
    discover_regional_csvs,
    export_selected_columns,
    read_preview_csv,
    result_image_entry,
)


class ResultsPreviewTests(unittest.TestCase):
    def test_discovers_only_regional_csvs_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            project = root / "project.qpproj"
            project.write_text("{}", encoding="utf-8")
            results = root / "brain_cell_counter_results"
            results.mkdir()
            older = results / "first_regional_counts_v1.0.csv"
            newer = results / "second_regional_counts_v1.1.csv"
            older.write_text("Image\nfirst\n", encoding="utf-8")
            newer.write_text("Image\nsecond\n", encoding="utf-8")
            older.touch()
            newer.touch()
            older_time = older.stat().st_mtime - 10
            import os

            os.utime(older, (older_time, older_time))
            self.assertEqual(discover_regional_csvs(project), [newer, older])

    def test_reads_utf8_bom_and_physical_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "result.csv"
            path.write_text(
                "\ufeffImage,Region_Area_mm2,DAPI_Density_per_mm2\n"
                "section-1,1.250000,80.0000\n",
                encoding="utf-8",
            )
            headers, rows, truncated = read_preview_csv(path)
            self.assertEqual(headers, ["Image", "Region_Area_mm2", "DAPI_Density_per_mm2"])
            self.assertEqual(rows[0]["Region_Area_mm2"], "1.250000")
            self.assertFalse(truncated)

    def test_row_limit_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "result.csv"
            path.write_text("Image\na\nb\n", encoding="utf-8")
            _headers, rows, truncated = read_preview_csv(path, row_limit=1)
            self.assertEqual(len(rows), 1)
            self.assertTrue(truncated)

    def test_extracts_one_exact_qupath_image_entry(self) -> None:
        rows = [
            {"Image": "651-0 Region 1.tif", "Acronym": "CA1"},
            {"Image": "651-0 Region 1.tif", "Acronym": "CA3"},
        ]
        self.assertEqual(result_image_entry(rows), "651-0 Region 1.tif")

    def test_rejects_ambiguous_result_image_entries(self) -> None:
        rows = [{"Image": "first.tif"}, {"Image": "second.tif"}]
        self.assertIsNone(result_image_entry(rows))

    def test_exports_only_selected_columns_for_all_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "full.csv"
            destination = root / "compact.csv"
            source.write_text(
                "Image,Acronym,Region_Area_mm2,DAPI,Technical_Field\n"
                "section.tif,CA1,1.2,100,unused\n"
                "section.tif,CA3,2.0,150,unused\n",
                encoding="utf-8",
            )
            row_count = export_selected_columns(
                source,
                destination,
                ["Image", "Acronym", "Region_Area_mm2", "DAPI"],
            )
            headers, rows, truncated = read_preview_csv(destination)
            self.assertEqual(headers, ["Image", "Acronym", "Region_Area_mm2", "DAPI"])
            self.assertEqual(row_count, 2)
            self.assertEqual(len(rows), 2)
            self.assertFalse(truncated)

    def test_compact_export_cannot_overwrite_master_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "master.csv"
            source.write_text("Image\nsection.tif\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                export_selected_columns(source, source, ["Image"])
            self.assertEqual(source.read_text(encoding="utf-8"), "Image\nsection.tif\n")


if __name__ == "__main__":
    unittest.main()
