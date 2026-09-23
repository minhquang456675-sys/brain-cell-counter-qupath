# Project file structure

The repository is grouped by responsibility so application source, scientific
scripts, documentation, installers, and generated files do not become mixed.

```text
Flourescent counting/
├── brain_cell_counter/          Python application package
│   ├── qt_app.py                Default PySide6 interface
│   ├── qt_results.py            Native Qt results preview and compact export
│   ├── hybrid_app.py            Classic compatibility interface
│   ├── app_core.py              Shared project and process controller
│   ├── threshold_optimizer.py   Global-threshold algorithms and reports
│   ├── calibration.py           Calibration data model and calculations
│   ├── calibration_wizard.py    Human-reviewed calibration workflow
│   ├── calibration_runner.py    Dedicated calibration-only launcher
│   ├── results_preview.py       Results table and QuPath-opening tools
│   ├── version.py               Application and output-schema versions
│   └── test_*.py                Automated regression tests
├── qupath_scripts/              Scripts executed by QuPath or Fiji
│   ├── Brain_Cell_Counter_QuPath_Engine_v1.0.groovy
│   ├── Brain_Cell_Counter_Calibration_Preview_v1.0.groovy
│   ├── Brain_Cell_Counter_Calibration_Apply_v1.0.groovy
│   └── Brain_Cell_Counter_Fiji_Calibration_v1.0.groovy
├── config/
│   └── functional_subregions_v1.json  Versioned curated Allen groupings
├── installer/                   Installer source and dependency verification
├── docs/                        Architecture, methods, installation, user guide
├── run_brain_cell_counter_v1.0.py  Stable Python launcher used by shortcuts
├── Start_Brain_Cell_Counter_v1.0.bat  Source-tree Windows launcher
├── README.md                    Project entry point
├── CHANGELOG.md                 Release history
└── AGENTS.md                    Repository-specific development rules
```

## Generated or local-only files

These files are intentionally outside source control:

- `dist/` and `installer/build/`: generated installer packages;
- `brain_cell_counter_settings.json`: machine-specific paths and preferences;
- `Brain Cell Counter.lnk`: local Windows shortcut;
- QuPath projects, TIFF images, results, logs, backups, and QC previews.

Edit source files in the folders above and rebuild generated packages. Never
edit copies extracted under `dist/`, `installer/build/`, or the installed
application directory.
