"""Test project, checkpoint, URI, and batch-event helpers used by the GUI."""

from __future__ import annotations

import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from brain_cell_counter.hybrid_app import HybridApp, qupath_gui_executable


class HybridHelperTests(unittest.TestCase):
    def test_prefers_windowed_qupath_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            console = root / "QuPath-0.7.0 (console).exe"
            gui = root / "QuPath-0.7.0.exe"
            console.touch()
            gui.touch()
            self.assertEqual(qupath_gui_executable(console), gui)

    def test_opens_exact_result_entry_in_qupath(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            console = root / "QuPath-0.7.0 (console).exe"
            gui = root / "QuPath-0.7.0.exe"
            console.touch()
            gui.touch()
            project = root / "project.qpproj"
            image_name = "651-0 Region 1.tif"
            project.write_text(
                json.dumps({"images": [{"imageName": image_name}]}),
                encoding="utf-8",
            )

            image_value = SimpleNamespace(value="", get=lambda: image_value.value)
            image_value.set = lambda value: setattr(image_value, "value", value)
            app = HybridApp.__new__(HybridApp)
            app.is_running = False
            app.values = {
                "qupath": SimpleNamespace(get=lambda: str(console)),
                "project": SimpleNamespace(get=lambda: str(project)),
                "image": image_value,
            }
            with patch("brain_cell_counter.hybrid_app.subprocess.Popen") as popen:
                app._open_result_in_qupath(project, image_name)

            popen.assert_called_once_with(
                [str(gui), "-p", str(project), "-i", image_name],
                cwd=str(gui.parent),
            )
            self.assertEqual(image_value.value, image_name)

    def test_file_uri_and_project_source_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "Region 1.tif"
            image.write_bytes(b"test")
            project = root / "project.qpproj"
            project.write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "imageName": image.name,
                                "serverBuilder": {
                                    "builder": {"uri": image.resolve().as_uri()}
                                },
                            },
                            {
                                "imageName": "Missing.tif",
                                "serverBuilder": {
                                    "builder": {"uri": (root / "Missing.tif").resolve().as_uri()}
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            app = HybridApp.__new__(HybridApp)
            _, scan = app._scan_project_sources(project)

            self.assertEqual([item["image_name"] for item in scan], ["Region 1.tif", "Missing.tif"])
            self.assertTrue(scan[0]["exists"])
            self.assertFalse(scan[1]["exists"])
            self.assertEqual(scan[0]["source_path"].resolve(), image.resolve())

    def test_checkpoint_round_trip_and_parameter_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.qpproj"
            project.write_text("{}", encoding="utf-8")
            checkpoint = HybridApp._analysis_checkpoint_path(project)
            signature = HybridApp._analysis_signature(project, ["--threshold=50"])
            other_signature = HybridApp._analysis_signature(project, ["--threshold=55"])

            self.assertNotEqual(signature, other_signature)
            HybridApp._write_analysis_checkpoint(
                checkpoint,
                project,
                signature,
                {"Region 1.tif", "Region 2.tif"},
                [{"image": "Region 3.tif", "error": "test error"}],
            )

            self.assertEqual(
                HybridApp._read_analysis_checkpoint(checkpoint, signature),
                {"Region 1.tif", "Region 2.tif"},
            )
            self.assertEqual(HybridApp._read_analysis_checkpoint(checkpoint, other_signature), set())

    def test_malformed_engine_event_is_ignored(self) -> None:
        self.assertIsNone(HybridApp._decode_bcc_event("ordinary QuPath output"))
        self.assertIsNone(HybridApp._decode_bcc_event("BCC_EVENT {broken json"))
        self.assertIsNone(HybridApp._decode_bcc_event("BCC_EVENT [1, 2, 3]"))
        self.assertEqual(
            HybridApp._decode_bcc_event('BCC_EVENT {"kind":"done","payload":{}}'),
            {"kind": "done", "payload": {}},
        )

    def test_batch_requires_the_expected_terminal_payload(self) -> None:
        self.assertIsNotNone(HybridApp._missing_batch_payload_message("validate", None, None))
        self.assertIsNone(HybridApp._missing_batch_payload_message("validate", {"ready": True}, None))
        self.assertIsNotNone(HybridApp._missing_batch_payload_message("analyze", None, None))
        self.assertIsNone(HybridApp._missing_batch_payload_message("analyze", None, {"csv": "result.csv"}))
        self.assertIn("ABBA import", HybridApp._missing_batch_payload_message("apply-atlas", None, None))

    def test_abba_registration_scan_requires_roi_transform_and_ontology(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.qpproj"
            project.write_text("{}", encoding="utf-8")
            (root / "Mouse-Ontology.json").write_text("{}", encoding="utf-8")
            complete = root / "data" / "1"
            complete.mkdir(parents=True)
            (complete / "ABBA-RoiSet-Mouse.zip").write_bytes(b"roi")
            (complete / "ABBA-Transform-Mouse.json").write_text("{}", encoding="utf-8")
            incomplete = root / "data" / "2"
            incomplete.mkdir(parents=True)
            (incomplete / "ABBA-RoiSet-Mouse.zip").write_bytes(b"roi")

            registered, diagnostics = HybridApp._scan_abba_registrations(
                project,
                {"Region 1.tif": 1, "Region 2.tif": 2},
            )

            self.assertEqual(registered, ["Region 1.tif"])
            self.assertTrue(diagnostics[0]["ready"])
            self.assertFalse(diagnostics[1]["ready"])
            self.assertFalse(diagnostics[1]["transform"])

    def test_checkpoint_failure_is_logged_without_raising(self) -> None:
        app = HybridApp.__new__(HybridApp)
        app.events = queue.Queue()
        with patch.object(HybridApp, "_write_analysis_checkpoint", side_effect=OSError("disk full")):
            written = app._safe_write_analysis_checkpoint(
                Path("checkpoint.json"),
                Path("project.qpproj"),
                "signature",
                {"Region 1.tif"},
                [],
            )
        self.assertFalse(written)
        kind, message = app.events.get_nowait()
        self.assertEqual(kind, "log")
        self.assertIn("disk full", message)


if __name__ == "__main__":
    unittest.main()
