<#
.SYNOPSIS
Installs the small Brain Cell Counter application payload and desktop shortcut.

.DESCRIPTION
Expands only the bundled application files. Large scientific dependencies are
handled by the separate numbered installer steps.
#>
[CmdletBinding()]
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$InstallRoot = Join-Path $env:LOCALAPPDATA "BrainCellCounter"
$PayloadArchive = Join-Path $PSScriptRoot "payload.zip"

function New-Shortcut {
    param(
        [string]$Path,
        [string]$TargetPath,
        [string]$Arguments,
        [string]$WorkingDirectory
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,14"
    $shortcut.Save()
}

try {
    Write-Host "Brain Cell Counter v2.1.3 - Core Installer" -ForegroundColor Cyan
    Write-Host "Destination: $InstallRoot"
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "64-bit Windows is required."
    }
    if (-not (Test-Path -LiteralPath $PayloadArchive)) {
        throw "payload.zip is missing. Keep all installer files in the same folder."
    }
    if ($DryRun) {
        Write-Host "Dry run passed. No files were changed." -ForegroundColor Green
        exit 0
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    Expand-Archive -LiteralPath $PayloadArchive -DestinationPath $InstallRoot -Force

    $launcher = Join-Path $InstallRoot "Launch_Brain_Cell_Counter.cmd"
    $launcherContent = @'
@echo off
set "BCC_ROOT=%LOCALAPPDATA%\BrainCellCounter"
set "BCC_PYTHON=%USERPROFILE%\miniforge3\envs\cellpose\pythonw.exe"
if exist "%BCC_PYTHON%" goto launch
for %%P in (pythonw.exe) do set "BCC_PYTHON=%%~$PATH:P"
if defined BCC_PYTHON goto launch
echo Python was not found.
echo Run 03_Install_Miniforge_Cellpose.cmd from the installation package first.
pause
exit /b 1
:launch
cd /d "%BCC_ROOT%"
start "Brain Cell Counter v2.1.3" "%BCC_PYTHON%" "%BCC_ROOT%\run_brain_cell_counter_v1.0.py"
'@
    Set-Content -LiteralPath $launcher -Value $launcherContent -Encoding ASCII

    $verifyLauncher = Join-Path $InstallRoot "Verify_Installation.cmd"
    Set-Content -LiteralPath $verifyLauncher -Encoding ASCII -Value @(
        "@echo off",
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallRoot\Verify_Brain_Cell_Counter_v1.0.ps1`"",
        "pause"
    )
    $desktop = [Environment]::GetFolderPath("Desktop")
    $cellposePythonw = Join-Path $env:USERPROFILE "miniforge3\envs\cellpose\pythonw.exe"
    $applicationScript = Join-Path $InstallRoot "run_brain_cell_counter_v1.0.py"
    $shortcutArguments = "`"$applicationScript`""
    New-Shortcut -Path (Join-Path $desktop "Brain Cell Counter.lnk") -TargetPath $cellposePythonw -Arguments $shortcutArguments -WorkingDirectory $InstallRoot
    New-Shortcut -Path (Join-Path $InstallRoot "Brain Cell Counter.lnk") -TargetPath $cellposePythonw -Arguments $shortcutArguments -WorkingDirectory $InstallRoot

    Write-Host ""
    Write-Host "Core installation completed." -ForegroundColor Green
    Write-Host "Desktop shortcut: Brain Cell Counter (v2.1.3)"
    Write-Host "Next: run 02_Install_QuPath_and_Extensions.cmd and 03_Install_Miniforge_Cellpose.cmd."
    exit 0
} catch {
    Write-Host "Core installation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
