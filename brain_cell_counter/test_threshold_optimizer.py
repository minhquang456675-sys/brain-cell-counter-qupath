"""Test stable-plateau, background-guard, and threshold-report behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain_cell_counter.threshold_optimizer import (
    calculate_global_thresholds,
    write_threshold_reports,
)


class ThresholdOptimizerTests(unittest.TestCase):
    def _profiles(self) -> list[dict]:
        thresholds = [0, 10, 20, 30, 40, 50, 60, 70]
        section_counts = [
            [100, 98, 97, 96, 80, 79, 78, 60],
            [100, 99, 98, 97, 81, 80, 79, 61],
            [100, 100, 99, 98, 82, 81, 80, 62],
        ]
        return [
            {
                "section_id": f"section-{index + 1}",
                "channels": [
                    {"key": "channel1", "name": "Channel 1", "thresholds": thresholds, "counts": counts},
                    {"key": "channel2", "name": "Channel 2", "thresholds": thresholds, "counts": counts},
                    {"key": "channel3", "name": "Channel 3", "thresholds": thresholds, "counts": counts},
                ],
            }
            for index, counts in enumerate(section_counts)
        ]

    def test_selects_highest_threshold_in_longest_plateau(self) -> None:
        result = calculate_global_thresholds(self._profiles())
        selected = {channel["name"]: channel["selected_threshold"] for channel in result["channels"]}
        self.assertEqual(
            selected,
            {"Channel 1": 30.0, "Channel 2": 30.0, "Channel 3": 30.0},
        )

    def test_writes_csv_json_and_html_reports(self) -> None:
        result = calculate_global_thresholds(self._profiles())
        with tempfile.TemporaryDirectory() as directory:
            reports = write_threshold_reports(result, Path(directory))
            self.assertTrue(all(Path(path).is_file() for path in reports.values()))

    def test_maximizes_double_positive_count_inside_stable_plateaus(self) -> None:
        profiles = self._profiles()
        thresholds = profiles[0]["channels"][0]["thresholds"]
        for section_index, profile in enumerate(profiles):
            profile["doublePositive"] = {
                "firstChannelKey": "channel1",
                "secondChannelKey": "channel2",
                "firstThresholds": thresholds,
                "secondThresholds": thresholds,
                "counts": [
                    [max(0, 80 - section_index - row * 8 - column * 5) for column in range(len(thresholds))]
                    for row in range(len(thresholds))
                ],
            }
        result = calculate_global_thresholds(
            profiles,
            objective="maximize_pair_overlap",
            pair_keys=("channel1", "channel2"),
        )
        overlap = result["double_positive_optimization"]
        self.assertEqual(overlap["selected_first_threshold"], 0.0)
        self.assertEqual(overlap["selected_second_threshold"], 0.0)
        self.assertEqual(result["channels"][0]["selected_threshold"], 0.0)
        self.assertEqual(result["channels"][1]["selected_threshold"], 0.0)
        self.assertEqual(result["channels"][2]["selected_threshold"], 30.0)
        with tempfile.TemporaryDirectory() as directory:
            reports = write_threshold_reports(result, Path(directory))
            self.assertTrue(Path(reports["double_positive_csv"]).is_file())

    def test_supports_channel1_channel3_pair(self) -> None:
        profiles = self._profiles()
        thresholds = profiles[0]["channels"][0]["thresholds"]
        for profile in profiles:
            profile["doublePositive"] = {
                "firstChannelKey": "channel1",
                "secondChannelKey": "channel3",
                "firstThresholds": thresholds,
                "secondThresholds": thresholds,
                "counts": [
                    [max(0, 50 - row * 4 - column * 3) for column in range(len(thresholds))]
                    for row in range(len(thresholds))
                ],
            }
        result = calculate_global_thresholds(
            profiles,
            objective="maximize_pair_overlap",
            pair_keys=("channel1", "channel3"),
        )
        overlap = result["double_positive_optimization"]
        self.assertEqual(overlap["first_channel_key"], "channel1")
        self.assertEqual(overlap["second_channel_key"], "channel3")
        self.assertEqual(result["channels"][1]["selected_threshold"], 30.0)

    def test_automatic_guard_rejects_saturated_low_threshold_plateau(self) -> None:
        result = calculate_global_thresholds(
            self._profiles(),
            automatic_background_guard=True,
        )
        for channel in result["channels"]:
            separation = channel["automatic_signal_separation"]
            self.assertIsNotNone(separation)
            self.assertGreater(separation["threshold"], 0.0)
            self.assertGreaterEqual(
                channel["selected_threshold"],
                separation["threshold"],
            )


if __name__ == "__main__":
    unittest.main()
