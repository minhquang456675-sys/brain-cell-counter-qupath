<#
.SYNOPSIS
Installs the pinned Miniforge and Cellpose-SAM runtime.

.DESCRIPTION
Creates the dedicated Python environment, selects a compatible PyTorch source,
and verifies Cellpose plus optional CUDA execution before reporting success.
#>
[CmdletBinding()]
param([switch]$DryRun, [switch]$SkipModelDownload)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$MiniforgeRoot = Join-Path $env:USERPROFILE "miniforge3"
$CondaExe = Join-Path $MiniforgeRoot "Scripts\conda.exe"
$CellposePython = Join-Path $MiniforgeRoot "envs\cellpose\python.exe"
$CacheRoot = Join-Path $env:LOCALAPPDATA "BrainCellCounter\installer-cache"
$InstallerPath = Join-Path $CacheRoot "Miniforge3-26.7.2-0-Windows-x86_64.exe"
$InstallerUrl = "https://github.com/conda-forge/miniforge/releases/download/26.7.2-0/Miniforge3-26.7.2-0-Windows-x86_64.exe"
$InstallerSha = "71cf9519087be74fa53021219ff292beb2fc05fa49e0bb6eb0e0b6b14fccbaab"

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$Description) {
    Write-Host $Description -ForegroundColor Yellow
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE." }
}

function Test-Nvidia {
    $command = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if ($null -eq $command) { return $false }
    & $command.Source --query-gpu=name --format=csv,noheader 2>$null
    return $LASTEXITCODE -eq 0
}

try {
    Write-Host "Miniforge + Cellpose-SAM Setup" -ForegroundColor Cyan
    Write-Host "This step downloads several GB and can take 20-60 minutes."
    if ($DryRun) { Write-Host "Dry run passed. No files were changed." -ForegroundColor Green; exit 0 }
    New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null

    if (-not (Test-Path -LiteralPath $CondaExe)) {
        $valid = (Test-Path -LiteralPath $InstallerPath) -and ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant() -eq $InstallerSha)
        if (-not $valid) {
            if (Test-Path -LiteralPath $InstallerPath) { Remove-Item -LiteralPath $InstallerPath -Force }
            $partial = "$InstallerPath.part"
            & "$env:SystemRoot\System32\curl.exe" -L --fail --retry 5 --retry-delay 3 --continue-at - --output $partial $InstallerUrl
            if ($LASTEXITCODE -ne 0) { throw "Miniforge download failed." }
            Move-Item -LiteralPath $partial -Destination $InstallerPath -Force
            if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $InstallerSha) { throw "Miniforge SHA-256 verification failed." }
        }
        $process = Start-Process -FilePath $InstallerPath -ArgumentList "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /NoRegistry=1 /S /D=$MiniforgeRoot" -Wait -PassThru
        if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $CondaExe)) { throw "Miniforge installation failed." }
    }

    if (-not (Test-Path -LiteralPath $CellposePython)) {
        Invoke-Checked $CondaExe @("create", "-y", "-n", "cellpose", "python=3.10", "pip", "tk") "Creating the cellpose environment"
    }
    $torchIndex = if (Test-Nvidia) { "https://download.pytorch.org/whl/cu128" } else { "https://download.pytorch.org/whl/cpu" }
    Write-Host "PyTorch source: $torchIndex" -ForegroundColor Green
    Invoke-Checked $CellposePython @("-m", "pip", "install", "--upgrade", "pip") "Updating pip"
    Invoke-Checked $CellposePython @("-m", "pip", "install", "torch==2.11.0", "torchvision==0.26.0", "--index-url", $torchIndex) "Installing PyTorch"
    Invoke-Checked $CellposePython @("-m", "pip", "install", "cellpose==4.2.1.1", "scikit-image==0.25.2", "pillow==10.3.0", "PySide6==6.11.2") "Installing Cellpose-SAM, calibration support, and the Qt interface"
    if (-not $SkipModelDownload) {
        Invoke-Checked $CellposePython @("-c", "from cellpose import models; models.CellposeModel(gpu=False, pretrained_model='cpsam_v2'); print('cpsam_v2 model ready')") "Downloading the cpsam_v2 model"
    }
    Invoke-Checked $CellposePython @("-c", "import torch,cellpose,skimage,tkinter,PIL,PySide6; print('torch=',torch.__version__); print('CUDA=',torch.cuda.is_available()); print('cellpose=',getattr(cellpose,'version',getattr(cellpose,'__version__','unknown'))); print('pillow=',PIL.__version__); print('PySide6=',PySide6.__version__)") "Verifying Cellpose, calibration support, and the Qt interface"
    Write-Host "Miniforge and Cellpose were installed successfully." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Cellpose setup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
