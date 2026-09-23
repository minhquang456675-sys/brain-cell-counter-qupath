<#
.SYNOPSIS
Builds the versioned modular installer folder and distributable ZIP archive.

.DESCRIPTION
Stages only source-controlled application files, creates payload.zip, and
writes a SHA-256 checksum for the final release archive.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$InstallerRoot = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $InstallerRoot
$DistRoot = Join-Path $ProjectRoot "dist"
$PackageRoot = Join-Path $DistRoot "Brain_Cell_Counter_v2.7.0_Installer_Package"
$PayloadStage = Join-Path ([IO.Path]::GetTempPath()) "BCC-v1-payload-stage"
$PayloadZip = Join-Path $PackageRoot "payload.zip"
$PackageZip = Join-Path $DistRoot "Brain_Cell_Counter_v2.7.0_Installer_Package.zip"

foreach ($path in @($PackageRoot, $PayloadStage)) {
    if (Test-Path -LiteralPath $path) {
        $full = [IO.Path]::GetFullPath($path)
        $allowed = $full.StartsWith([IO.Path]::GetFullPath($DistRoot), [StringComparison]::OrdinalIgnoreCase) -or
            $full.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)
        if (-not $allowed) { throw "Refusing to clear unexpected path: $path" }
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}
if (Test-Path -LiteralPath $PackageZip) { Remove-Item -LiteralPath $PackageZip -Force }
New-Item -ItemType Directory -Path $PackageRoot, $PayloadStage -Force | Out-Null

foreach ($file in @(
    "run_brain_cell_counter_v1.0.py",
    "README.md",
    "CHANGELOG.md"
)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $file) -Destination $PayloadStage
}
$scriptStage = Join-Path $PayloadStage "qupath_scripts"
New-Item -ItemType Directory -Path $scriptStage -Force | Out-Null
Copy-Item -Path (Join-Path $ProjectRoot "qupath_scripts\*.groovy") -Destination $scriptStage
$packageStage = Join-Path $PayloadStage "brain_cell_counter"
New-Item -ItemType Directory -Path $packageStage -Force | Out-Null
foreach ($file in @("__init__.py", "version.py", "app_core.py", "qt_app.py", "qt_results.py", "qt_calibration.py", "hybrid_app.py", "threshold_optimizer.py", "calibration.py", "calibration_wizard.py", "calibration_runner.py", "results_preview.py")) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "brain_cell_counter\$file") -Destination $packageStage
}
foreach ($file in @(
    "Verify_Brain_Cell_Counter_v1.0.ps1",
    "THIRD_PARTY_NOTICES.md",
    "installer_manifest.json",
    "STEP_BY_STEP_INSTALLATION_GUIDE.html"
)) {
    Copy-Item -LiteralPath (Join-Path $InstallerRoot $file) -Destination $PayloadStage
}
Compress-Archive -Path (Join-Path $PayloadStage "*") -DestinationPath $PayloadZip -CompressionLevel Optimal

foreach ($file in @(
    "01_Install_Core.cmd", "Install_Core.ps1",
    "02_Install_QuPath_and_Extensions.cmd", "Install_QuPath_and_Extensions.ps1",
    "Merge_QuPath_Extension_Registry.ps1",
    "03_Install_Miniforge_Cellpose.cmd", "Install_Miniforge_Cellpose.ps1",
    "04_Verify_Installation.cmd", "Verify_Brain_Cell_Counter_v1.0.ps1",
    "STEP_BY_STEP_INSTALLATION_GUIDE.html", "README_INSTALLER.md",
    "THIRD_PARTY_NOTICES.md", "installer_manifest.json"
)) {
    Copy-Item -LiteralPath (Join-Path $InstallerRoot $file) -Destination $PackageRoot
}

Compress-Archive -Path $PackageRoot -DestinationPath $PackageZip -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $PackageZip -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $DistRoot "Brain_Cell_Counter_v2.7.0_Installer_Package.sha256.txt") -Encoding ASCII -Value "$hash  Brain_Cell_Counter_v2.7.0_Installer_Package.zip"
Write-Host "Built folder: $PackageRoot"
Write-Host "Built package: $PackageZip"
Write-Host "SHA-256: $hash"
