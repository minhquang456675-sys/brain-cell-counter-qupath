# Installation and dependency baseline

## Requirements

- 64-bit Windows 10 or Windows 11
- approximately 20 GB of free disk space
- stable internet connection
- current NVIDIA display driver for GPU processing

The separate CUDA Toolkit is not required by the installer.

## Install on another computer

Copy `dist/Brain_Cell_Counter_v2.5.1_Installer_Package.zip` to the target
computer, extract it, and open `STEP_BY_STEP_INSTALLATION_GUIDE.html`.

Run the numbered steps in order:

1. `01_Install_Core.cmd`
2. `02_Install_QuPath_and_Extensions.cmd`
3. `03_Install_Miniforge_Cellpose.cmd`
4. Install the official ABBA 0.11.0 package as described in the guide. It
   includes Fiji, DeepSlice Local, and Elastix.
5. `04_Verify_Installation.cmd`

The core installer is intentionally small. Large dependencies are downloaded
from official pinned releases and verified with SHA-256. Interrupted downloads
are retained under `%LOCALAPPDATA%\BrainCellCounter\installer-cache` and can be
resumed by running the same step again.

The final verification report is written to:

```text
%LOCALAPPDATA%\BrainCellCounter\Installation_Report.txt
```

## Pinned baseline

| Component | Version |
|---|---|
| Python | 3.10 |
| PySide6 | 6.11.2 |
| QuPath | 0.7.0 |
| Cellpose | 4.2.1.1 |
| scikit-image | 0.25.2 |
| Pillow | 10.3.0 |
| QuPath Cellpose extension | 0.12.1 |
| ABBA | 0.11.0 |
| QuPath ABBA extension | 0.5.0 |
| Warpy extension | 0.5.0 |

The installer selects a CUDA-enabled PyTorch source when a supported NVIDIA GPU
is detected and otherwise uses the CPU source. Keep these versions pinned for
reproducibility unless an upgrade has been explicitly validated.

## Build policy

Installer source belongs in `installer/`. Generated contents under
`installer/build/` and `dist/` are ignored by Git and must not be edited
directly. Rebuild them from source when creating a release.

See [`../installer/README_INSTALLER.md`](../installer/README_INSTALLER.md) and
[`../installer/THIRD_PARTY_NOTICES.md`](../installer/THIRD_PARTY_NOTICES.md).
