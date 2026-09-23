# Brain Cell Counter v2.3.0 — Windows installer package

Open `STEP_BY_STEP_INSTALLATION_GUIDE.html`, then run the numbered files in
order:

1. `01_Install_Core.cmd` — installs the small Brain Cell Counter application
   and Desktop shortcut.
2. `02_Install_QuPath_and_Extensions.cmd` — installs QuPath 0.7.0 and the
   required BIOP extensions.
3. `03_Install_Miniforge_Cellpose.cmd` — installs Miniforge, PyTorch and
   Cellpose-SAM plus PySide6 for the version 2 interface. This is the longest
   automated step.
4. Install the official ABBA 0.11.0 package manually by following the guide.
   That package already includes Fiji, DeepSlice Local and Elastix.
5. `04_Verify_Installation.cmd` — checks all installed components and CUDA.

Requirements are 64-bit Windows 10/11, 20 GB free disk space and a stable
internet connection. An NVIDIA display driver is required for GPU processing;
the separate CUDA Toolkit is not required.

Downloads are pinned to tested versions and verified with SHA-256. Interrupted
downloads are retained under
`%LOCALAPPDATA%\BrainCellCounter\installer-cache` and can be resumed by
rerunning the same numbered file.

The final report is written to
`%LOCALAPPDATA%\BrainCellCounter\Installation_Report.txt`.

The classic Tk interface remains installed as a compatibility path for
calibration, threshold-audit, and complete Results Preview workflows while
their Qt pages are migrated.
