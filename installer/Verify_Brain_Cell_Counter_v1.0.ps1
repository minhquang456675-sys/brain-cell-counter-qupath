<#
.SYNOPSIS
Verifies the installed Brain Cell Counter dependencies and writes a report.

.DESCRIPTION
Checks application files, QuPath extensions, Cellpose, PySide6, Pillow, and CUDA without
changing the scientific installation.
#>
[CmdletBinding()]
param([switch]$NonInteractive)

$ErrorActionPreference = "Continue"
$InstallRoot = Join-Path $env:LOCALAPPDATA "BrainCellCounter"
$CellposePython = Join-Path $env:USERPROFILE "miniforge3\envs\cellpose\python.exe"
$QuPathConsole = Join-Path $env:LOCALAPPDATA "QuPath-0.7.0\QuPath-0.7.0 (console).exe"
$ExtensionRoot = Join-Path $env:USERPROFILE "QuPath\v0.7\extensions"
$AbbaRoot = Join-Path $env:LOCALAPPDATA "ABBA-Python-0.11.0"
$CellposeModel = Join-Path $env:USERPROFILE ".cellpose\models\cpsam_v2"
$ReportPath = Join-Path $InstallRoot "Installation_Report.txt"
$results = New-Object System.Collections.Generic.List[string]
$failed = $false

function Add-Check {
    param([string]$Name, [bool]$Passed, [string]$Details)
    $status = if ($Passed) { "PASS" } else { "FAIL" }
    $script:results.Add("[$status] $Name - $Details")
    if (-not $Passed) { $script:failed = $true }
}

Add-Check "Brain Cell Counter files" (Test-Path -LiteralPath (Join-Path $InstallRoot "run_brain_cell_counter_v1.0.py")) $InstallRoot
Add-Check "Miniforge Cellpose Python" (Test-Path -LiteralPath $CellposePython) $CellposePython
Add-Check "Cellpose-SAM v2 model" (Test-Path -LiteralPath $CellposeModel) $CellposeModel
Add-Check "QuPath 0.7.0" (Test-Path -LiteralPath $QuPathConsole) $QuPathConsole
Add-Check "QuPath Cellpose extension" ($null -ne (Get-ChildItem -LiteralPath $ExtensionRoot -Recurse -File -Filter "qupath-extension-cellpose-0.12.1.jar" -ErrorAction SilentlyContinue | Select-Object -First 1)) "v0.12.1"
Add-Check "QuPath ABBA extension" ($null -ne (Get-ChildItem -LiteralPath $ExtensionRoot -Recurse -File -Filter "qupath-extension-abba-0.5.0.jar" -ErrorAction SilentlyContinue | Select-Object -First 1)) "v0.5.0"
Add-Check "QuPath Warpy dependency" ($null -ne (Get-ChildItem -LiteralPath $ExtensionRoot -Recurse -File -Filter "qupath-extension-warpy-0.5.0.jar" -ErrorAction SilentlyContinue | Select-Object -First 1)) "v0.5.0"
$fiji = Get-ChildItem -LiteralPath $AbbaRoot -Recurse -File -Filter "ImageJ-win64.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
$deepSlice = Get-ChildItem -LiteralPath $AbbaRoot -Recurse -Directory -Filter "deepslice" -ErrorAction SilentlyContinue | Select-Object -First 1
Add-Check "Fiji / ABBA" ($null -ne $fiji) $(if ($fiji) { $fiji.FullName } else { $AbbaRoot })
Add-Check "DeepSlice Local" ($null -ne $deepSlice) $(if ($deepSlice) { $deepSlice.FullName } else { "Not found under $AbbaRoot" })

if (Test-Path -LiteralPath $CellposePython) {
    $pythonCheck = & $CellposePython -c "import json,torch,cellpose,skimage,tkinter,PIL,PySide6; print(json.dumps({'torch':torch.__version__,'cuda_runtime':torch.version.cuda,'cuda_available':torch.cuda.is_available(),'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'cellpose':getattr(cellpose,'version',getattr(cellpose,'__version__','unknown')),'skimage':skimage.__version__,'pillow':PIL.__version__,'pyside6':PySide6.__version__}))" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Add-Check "Python imports" $true ($pythonCheck -join " ")
    } else {
        Add-Check "Python imports" $false ($pythonCheck -join " ")
    }
}

$header = @(
    "Brain Cell Counter v2.6.0 - Installation Report",
    "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "Computer: $env:COMPUTERNAME",
    "User: $env:USERNAME",
    ""
)
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
Set-Content -LiteralPath $ReportPath -Value ($header + $results) -Encoding UTF8
$results | ForEach-Object {
    if ($_ -like "[[]PASS[]]*") { Write-Host $_ -ForegroundColor Green } else { Write-Host $_ -ForegroundColor Red }
}
Write-Host "Report: $ReportPath"
if ($failed) { exit 1 } else { exit 0 }
