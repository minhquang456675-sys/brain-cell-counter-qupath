"""Tests for UI-independent project parsing and command construction."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brain_cell_counter.app_core import (
    analysis_arguments,
    analysis_signature,
    qupath_command,
    read_checkpoint,
    read_project,
    write_checkpoint,
)


class AppCoreTests(unittest.TestCase):
    def test_read_project_preserves_entry_order_and_abba_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "section one.tif"
            image.write_bytes(b"test")
            (root / "data" / "7").mkdir(parents=True)
            (root / "data" / "7" / "ABBA-RoiSet-test.json").write_text("{}")
            (root / "data" / "7" / "ABBA-Transform-test.json").write_text("{}")
            (root / "Allen-Ontology.json").write_text("{}")
            project = root / "project.qpproj"
            project.write_text(json.dumps({"images": [{
                "imageName": "Section 1",
                "entryID": 7,
                "serverBuilder": {"uri": image.as_uri()},
            }]}), encoding="utf-8")
            summary = read_project(project)
            self.assertEqual([entry.name for entry in summary.entries], ["Section 1"])
            self.assertTrue(summary.entries[0].source_exists)
            self.assertTrue(summary.entries[0].abba_ready)
            self.assertEqual(summary.analysis_ready_count, 1)

    def test_arguments_keep_channel_mapping_and_scientific_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory) / "python.exe"
            python.write_bytes(b"")
            settings = {
                "cellpose_python": str(python),
                "channel_1_name": "NeuN",
                "channel_2_name": "c-Fos",
                "channel_3_name": "DAPI",
                "channel_1_threshold": "45",
                "channel_2_threshold": "70",
                "channel_3_threshold": "35",
                "cellprob": "-0.5",
                "flow": "0.4",
                "cellpose_model": "cpsam_v2",
                "reuse_detections": True,
                "hemisphere_mode": "Left and Right",
            }
            arguments = analysis_arguments(settings, "analyze")
            self.assertIn("neunChannel=1", arguments)
            self.assertIn("cfosChannel=2", arguments)
            self.assertIn("dapiChannel=3", arguments)
            self.assertIn("neunThreshold=45.0", arguments)
            self.assertIn("hemisphereMode=left-right", arguments)
            self.assertIn("reuseDetections=true", arguments)
            self.assertIn("cellposeModel=cpsam_v2", arguments)

    def test_arguments_reject_unknown_cellpose_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory) / "python.exe"
            python.write_bytes(b"")
            settings = {
                "cellpose_python": str(python),
                "channel_1_name": "Channel 1",
                "channel_2_name": "Channel 2",
                "channel_3_name": "Channel 3",
                "channel_1_threshold": "50",
                "channel_2_threshold": "50",
                "channel_3_threshold": "50",
                "cellpose_model": "unknown",
            }
            with self.assertRaisesRegex(ValueError, "Unsupported Cellpose model"):
                analysis_arguments(settings, "analyze")

    def test_qupath_command_places_save_before_engine(self) -> None:
        command = qupath_command(
            Path("QuPath.exe"), Path("project.qpproj"), "Region 1", Path("engine.groovy"),
            ["mode=analyze", "flowThreshold=0.4"], save=True,
        )
        self.assertEqual(command[:7], ["QuPath.exe", "-q", "script", "-p", "project.qpproj", "-i", "Region 1"])
        self.assertLess(command.index("--save"), command.index("engine.groovy"))
        self.assertEqual(command[-4:], ["--args", "mode=analyze", "--args", "flowThreshold=0.4"])

    def test_checkpoint_requires_exact_analysis_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project.qpproj"
            project.write_text("{}", encoding="utf-8")
            path = root / "checkpoint.json"
            signature = analysis_signature(project, ["mode=analyze", "flowThreshold=0.4"])
            write_checkpoint(path, project, signature, {"Section 1"}, [])
            self.assertEqual(read_checkpoint(path, signature), {"Section 1"})
            self.assertEqual(read_checkpoint(path, "different-parameters"), set())


if __name__ == "__main__":
    unittest.main()
