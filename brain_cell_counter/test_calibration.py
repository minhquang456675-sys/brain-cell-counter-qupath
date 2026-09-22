"""Test scale-bar detection, calibration arithmetic, matching, and manifests."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from brain_cell_counter.calibration import (
    CALIBRATION_SUMMARY_FIELDS,
    CalibrationRecord,
    detect_scale_bar,
    match_records_to_project,
    next_fiji_output_path,
    valid_confirmed_fiji_records,
    valid_confirmed_records,
    write_calibration_audit,
    write_calibration_manifest,
)


class CalibrationTests(unittest.TestCase):
    def test_detects_vertical_scale_bar_near_margin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.png"
            image = Image.new("RGB", (500, 320), "black")
            draw = ImageDraw.Draw(image)
            draw.line((12, 210, 12, 300), fill="white", width=3)
            draw.line((6, 210, 18, 210), fill="white", width=2)
            draw.line((6, 300, 18, 300), fill="white", width=2)
            image.save(path)

            candidate = detect_scale_bar(path)

            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertEqual(candidate.orientation, "vertical")
            self.assertAlmostEqual(candidate.preview_length_pixels, 90.0, delta=2.0)

    def test_proposal_accounts_for_preview_downsample_and_units(self) -> None:
        record = CalibrationRecord(
            source_path="image.tif",
            downsample=4.0,
            bar_preview_length_pixels=250.0,
            known_distance=0.5,
            known_unit="mm",
        )
        self.assertAlmostEqual(record.calculate_proposal(), 0.5)

    def test_only_confirmed_project_records_can_be_applied(self) -> None:
        valid = CalibrationRecord(
            source_path="a.tif",
            image_name="A",
            entry_id=1,
            proposed_pixel_size_microns=0.4,
            confirmed=True,
        )
        unconfirmed = CalibrationRecord(
            source_path="b.tif",
            image_name="B",
            entry_id=2,
            proposed_pixel_size_microns=0.5,
            confirmed=False,
        )
        self.assertEqual(valid_confirmed_records([valid, unconfirmed]), [valid])

    def test_fiji_export_does_not_require_qupath_project_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "section.ome.tif"
            source.write_bytes(b"image")
            record = CalibrationRecord(
                source_path=str(source),
                proposed_pixel_size_microns=0.4,
                confirmed=True,
            )
            self.assertEqual(valid_confirmed_fiji_records([record]), [record])

    def test_fiji_output_never_overwrites_existing_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "section_calibrated.ome.tif"
            first.write_bytes(b"existing")
            output = next_fiji_output_path(root, Path("section.tif"))
            self.assertEqual(output.name, "section_calibrated_2.ome.tif")

    def test_matches_exact_project_source_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Region 1.tif"
            source.write_bytes(b"test")
            project = root / "project.qpproj"
            project.write_text(
                json.dumps({
                    "images": [{
                        "entryID": 1,
                        "imageName": "Region 1.tif",
                        "serverBuilder": {"builder": {"uri": source.resolve().as_uri()}},
                    }]
                }),
                encoding="utf-8",
            )
            record = CalibrationRecord(source_path=str(source))

            match_records_to_project([record], project)
            manifest = write_calibration_manifest([record], root / "calibration.csv")

            self.assertEqual(record.image_name, "Region 1.tif")
            self.assertEqual(record.entry_id, 1)
            self.assertTrue(manifest.is_file())
            self.assertIn("source_path", manifest.read_text(encoding="utf-8-sig"))

    def test_manifest_exports_pixel_width_and_height_in_millimetres(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "calibration.csv"
            record = CalibrationRecord(
                source_path="section.tif",
                proposed_pixel_size_microns=0.5,
                confirmed=True,
                fiji_verified=True,
                fiji_verified_pixel_width_microns=0.6,
                fiji_verified_pixel_height_microns=0.7,
            )

            write_calibration_manifest([record], output)

            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                row = next(reader)
                self.assertEqual(tuple(reader.fieldnames or ()), CALIBRATION_SUMMARY_FIELDS)
            self.assertAlmostEqual(float(row["pixel_width_mm"]), 0.0006)
            self.assertAlmostEqual(float(row["pixel_height_mm"]), 0.0007)

    def test_full_calibration_state_is_preserved_in_json_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "calibration_audit_v1.2.json"
            record = CalibrationRecord(
                source_path="section.tif",
                bar_start_x=12,
                bar_start_y=210,
                proposed_pixel_size_microns=0.5,
                confirmed=True,
            )

            write_calibration_audit([record], output)

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schemaVersion"], "1.2")
            self.assertEqual(payload["records"][0]["bar_start_x"], 12)


if __name__ == "__main__":
    unittest.main()
