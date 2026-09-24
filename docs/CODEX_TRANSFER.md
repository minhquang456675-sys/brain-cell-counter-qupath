# Transfer Brain Cell Counter to another Codex computer

This package transfers the application source, full local Git history, Codex
repository instructions, and the Windows installer. It intentionally excludes
microscopy images, QuPath projects, generated results, credentials, caches, and
machine-specific settings.

## Package contents

- `Brain_Cell_Counter_Source_vX.Y.Z.zip`: source snapshot at the recorded commit.
- `Brain_Cell_Counter_History_vX.Y.Z.bundle`: complete local Git repository history.
- `Brain_Cell_Counter_vX.Y.Z_Installer_Package.zip`: Windows application and dependency installer.
- `TRANSFER_MANIFEST.json`: release, commit, branch, and build-time metadata.
- `SHA256SUMS.txt`: checksums for every transferred artifact.

## Restore the repository for Codex

1. Copy the complete transfer ZIP to the other computer and extract it.
2. Open PowerShell in the extracted folder.
3. Restore the working repository:

   ```powershell
   git clone .\Brain_Cell_Counter_History_vX.Y.Z.bundle "Flourescent counting"
   ```

4. Open the restored `Flourescent counting` folder as a Codex project.
5. Codex must read the root `AGENTS.md`, then the task-relevant documentation
   under `docs/`, before modifying the application.
6. Run the automated source verification:

   ```powershell
   python -m unittest discover -s brain_cell_counter -p "test_*.py"
   ```

The source ZIP is a convenient read-only snapshot. Use the Git bundle for
continued development because it preserves commit history.

## Install the scientific application stack

1. Extract `Brain_Cell_Counter_vX.Y.Z_Installer_Package.zip`.
2. Open `STEP_BY_STEP_INSTALLATION_GUIDE.html`.
3. Run the numbered installers in order.
4. Install the official ABBA package using the manual step in the guide.
5. Run `04_Verify_Installation.cmd`.

The installer downloads large pinned dependencies from their official sources.
It does not bundle raw microscopy data or QuPath projects.

## Move experimental data separately

Copy QuPath projects, TIFF/OME-TIFF images, ABBA exports, and result folders as
a separate data transfer. Preserve their relative folder structure. Do not add
these files to the source repository.

On the new computer, start Brain Cell Counter and select the copied
`project.qpproj`. Reconfigure QuPath, Fiji, and Cellpose paths in Settings.
Do not copy `brain_cell_counter_settings.json` from the old computer because it
contains machine-specific absolute paths.

## Integrity check

In PowerShell, compare each artifact with `SHA256SUMS.txt`:

```powershell
Get-FileHash .\Brain_Cell_Counter_History_vX.Y.Z.bundle -Algorithm SHA256
Get-FileHash .\Brain_Cell_Counter_Source_vX.Y.Z.zip -Algorithm SHA256
Get-FileHash .\Brain_Cell_Counter_vX.Y.Z_Installer_Package.zip -Algorithm SHA256
```

Do not continue if a checksum differs.
