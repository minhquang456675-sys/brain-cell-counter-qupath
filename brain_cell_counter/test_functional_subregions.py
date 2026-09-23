"""Validate the versioned curated functional-subregion configuration."""

from __future__ import annotations

import json
import unittest

from brain_cell_counter.app_core import functional_subregion_config_path


class FunctionalSubregionConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(
            functional_subregion_config_path().read_text(encoding="utf-8")
        )

    def test_config_has_unique_nonempty_groups_and_roots(self) -> None:
        groups = self.config["groups"]
        names = [group["name"] for group in groups]
        roots = [root for group in groups for root in group["root_acronyms"]]
        self.assertEqual(self.config["schema_version"], "1.0")
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(roots), len(set(roots)))
        self.assertTrue(all(names))
        self.assertTrue(all(group["root_acronyms"] for group in groups))

    def test_expected_curated_groups_are_present(self) -> None:
        groups = {
            group["name"]: set(group["root_acronyms"])
            for group in self.config["groups"]
        }
        self.assertEqual(groups["mPFC"], {"ACA", "PL", "ILA"})
        self.assertEqual(groups["Hippocampus_CA"], {"CA1", "CA2", "CA3"})
        self.assertEqual(groups["Ventral_Striatum"], {"ACB", "OT"})
        self.assertEqual(groups["Basolateral_Amygdala"], {"BLA", "BMA", "LA"})


if __name__ == "__main__":
    unittest.main()
