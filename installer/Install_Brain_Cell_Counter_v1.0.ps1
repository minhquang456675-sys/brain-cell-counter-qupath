<#
.SYNOPSIS
Runs the legacy all-in-one Brain Cell Counter installation workflow.

.DESCRIPTION
Installs the application and pinned scientific dependencies with verified
downloads. The modular numbered installer remains the preferred user path.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipModelDownload
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ProductName = "Brain Cell Counter v2.0.0"
$InstallRoot = Join-Path $env:LOCALAPPDATA "BrainCellCounter"
$CacheRoot = Join-Path $InstallRoot "installer-cache"
$MiniforgeRoot = Join-Path $env:USERPROFILE "miniforge3"
$CellposePython = Join-Path $MiniforgeRoot "envs\cellpose\python.exe"
$QuPathRoot = Join-Path $env:LOCALAPPDATA "QuPath-0.7.0"
$QuPathConsole = Join-Path $QuPathRoot "QuPath-0.7.0 (console).exe"
$AbbaRoot = Join-Path $env:LOCALAPPDATA "ABBA-Python-0.11.0"
$PayloadArchive = Join-Path $PSScriptRoot "payload.zip"
$LogPath = Join-Path $env:TEMP "Brain_Cell_Counter_v2.0.0_install.log"
. (Join-Path $PSScriptRoot "Merge_QuPath_Extension_Registry.ps1")
$script:StepNumber = 0
$script:TotalSteps = 9

$Packages = @{
    Miniforge = @{
        Url = "https://github.com/conda-forge/miniforge/releases/download/26.7.2-0/Miniforge3-26.7.2-0-Windows-x86_64.exe"
        Sha256 = "71cf9519087be74fa53021219ff292beb2fc05fa49e0bb6eb0e0b6b14fccbaab"
        File = "Miniforge3-26.7.2-0-Windows-x86_64.exe"
    }
    QuPath = @{
        Url = "https://github.com/qupath/qupath/releases/download/v0.7.0/QuPath-v0.7.0-Windows.zip"
        Sha256 = "5839743281d11eca1ac55c87ddcf92c89a30cba7ba2060f162036b0514b1e30c"
        File = "QuPath-v0.7.0-Windows.zip"
    }
    Abba = @{
        Url = "https://github.com/BIOP/ijp-imagetoatlas/releases/download/0.11.0/ABBA-Python-0.11.0-0.11.0-Windows-x86_64.exe"
        Sha256 = "97a79635d89606d5096304801584ab81f9e63036b0b571f4313ab71bbba1c9ab"
        File = "ABBA-Python-0.11.0-0.11.0-Windows-x86_64.exe"
    }
    CellposeExtension = @{
        Url = "https://github.com/BIOP/qupath-extension-cellpose/releases/download/v0.12.1/qupath-extension-cellpose-0.12.1.zip"
        Sha256 = "9b31ccc616f07740a52d1e4f0c6da83a14d1011b35b70b5ba847113b6fcb63da"
        File = "qupath-extension-cellpose-0.12.1.zip"
    }
    AbbaExtension = @{
        Url = "https://github.com/BIOP/qupath-extension-abba/releases/download/0.5.0/qupath-extension-abba-0.5.0.jar"
        Sha256 = "0e27fc8486bfb20b917c787c4bd5d8463aefd4f3ec0bf5bb160436af7a53b1d3"
        File = "qupath-extension-abba-0.5.0.jar"
    }
    WarpyExtension = @{
        Url = "https://github.com/BIOP/qupath-extension-warpy/releases/download/0.5.0/qupath-extension-warpy-0.5.0.jar"
        Sha256 = "e4bc1c636adbcfd6e6dafde32d615afa032d3c0a21458660b2de380a884f5cb8"
        File = "qupath-extension-warpy-0.5.0.jar"
    }
}

function Write-InstallLog {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::Gray)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] $Message" -ForegroundColor $Color
    Add-Content -LiteralPath $LogPath -Value "[$timestamp] $Message" -Encoding UTF8
}

function Write-Step {
    param([string]$Message)
    $script:StepNumber++
    Write-InstallLog "STEP $script:StepNumber/$script:TotalSteps - $Message" Cyan
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$Description = $FilePath
    )
    Write-InstallLog $Description DarkGray
    & $FilePath @Arguments 2>&1 | ForEach-Object {
        Write-Host $_
        Add-Content -LiteralPath $LogPath -Value $_ -Encoding UTF8
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Test-FileHashExact {
    param([string]$Path, [string]$ExpectedSha256)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    return $actual -eq $ExpectedSha256.ToLowerInvariant()
}

function Get-VerifiedPackage {
    param([hashtable]$Package)
    $destination = Join-Path $CacheRoot $Package.File
    if (Test-FileHashExact -Path $destination -ExpectedSha256 $Package.Sha256) {
        Write-InstallLog "Using verified cached package: $($Package.File)" Green
        return $destination
    }
    if (Test-Path -LiteralPath $destination) {
        Remove-Item -LiteralPath $destination -Force
    }
    $partial = "$destination.part"
    Write-InstallLog "Downloading $($Package.File)..." Yellow
    $curlArguments = @(
        "-L", "--fail", "--retry", "5", "--retry-delay", "3",
        "--continue-at", "-", "--output", $partial, $Package.Url
    )
    Invoke-Checked -FilePath "$env:SystemRoot\System32\curl.exe" -Arguments $curlArguments -Description "Download $($Package.File)"
    Move-Item -LiteralPath $partial -Destination $destination -Force
    if (-not (Test-FileHashExact -Path $destination -ExpectedSha256 $Package.Sha256)) {
        Remove-Item -LiteralPath $destination -Force
        throw "SHA-256 verification failed for $($Package.File)."
    }
    Write-InstallLog "Verified SHA-256: $($Package.File)" Green
    return $destination
}

function Get-NvidiaGpuNames {
    $nvidiaSmi = Get-Command "nvidia-smi.exe" -ErrorAction SilentlyContinue
    if ($null -eq $nvidiaSmi) {
        return @()
    }
    try {
        return @(& $nvidiaSmi.Source "--query-gpu=name" "--format=csv,noheader" 2>$null | Where-Object { $_.Trim() })
    } catch {
        return @()
    }
}

function New-Shortcut {
    param([string]$Path, [string]$TargetPath, [string]$WorkingDirectory, [string]$IconLocation)
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.WorkingDirectory = $WorkingDirectory
    if ($IconLocation) {
        $shortcut.IconLocation = $IconLocation
    }
    $shortcut.Save()
}

function Write-InitialSettings {
    $settingsPath = Join-Path $InstallRoot "brain_cell_counter_settings.json"
    if (Test-Path -LiteralPath $settingsPath) {
        Write-InstallLog "Keeping existing Brain Cell Counter settings." Green
        return
    }
    $settings = [ordered]@{
        project = ""
        channel_names = [ordered]@{ "1" = "Channel 1"; "2" = "Channel 2"; "3" = "Channel 3" }
        hemisphere_mode = "Left and Right"
        global_thresholds = [ordered]@{ channel1 = "50"; channel2 = "50"; channel3 = "50"; locked = $false }
        cellpose_parameters = [ordered]@{ cellprob = "-0.5"; flow = "0.4" }
        ui_settings = [ordered]@{
            font_size = "9"; log_font_size = "9"; window_width = "1080"; window_height = "820"
            window_size_preset = "Recommended (automatic monitor fit)"
        }
        threshold_optimizer = [ordered]@{
            objective = "Individual channels"; automatic_background_guard = $true
            scope = "Current project — all entries"; project = ""
            channel1_start = "0"; channel1_end = "200"; channel1_step = "5"
            channel2_start = "0"; channel2_end = "200"; channel2_step = "5"
            channel3_start = "0"; channel3_end = "200"; channel3_step = "5"
            total_change_limit = "3"; section_change_limit = "5"
            stable_section_percent = "90"; minimum_stable_steps = "3"
        }
    }
    $settings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $settingsPath -Encoding UTF8
}

try {
    Clear-Content -LiteralPath $LogPath -ErrorAction SilentlyContinue
    Write-InstallLog "$ProductName complete Windows installer" White
    Write-InstallLog "Log: $LogPath" DarkGray

    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "$ProductName requires 64-bit Windows."
    }
    $os = Get-CimInstance Win32_OperatingSystem
    Write-InstallLog "Windows: $($os.Caption) $($os.Version)" DarkGray
    $driveRoot = [IO.Path]::GetPathRoot($env:LOCALAPPDATA)
    $drive = New-Object IO.DriveInfo($driveRoot)
    $freeGb = [Math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    Write-InstallLog "Free space on ${driveRoot}: $freeGb GB" DarkGray
    if ($freeGb -lt 20) {
        throw "At least 20 GB of free space is required. Current free space: $freeGb GB."
    }
    if (-not (Test-Path -LiteralPath $PayloadArchive)) {
        throw "Installer payload.zip is missing. Run the original setup package again."
    }

    Write-Step "Preparing application folders"
    if ($DryRun) {
        Write-InstallLog "Dry run completed before any system changes." Green
        exit 0
    }
    New-Item -ItemType Directory -Path $InstallRoot, $CacheRoot -Force | Out-Null
    Expand-Archive -LiteralPath $PayloadArchive -DestinationPath $InstallRoot -Force

    Write-Step "Installing Miniforge 26.7.2-0"
    $condaExe = Join-Path $MiniforgeRoot "Scripts\conda.exe"
    if (-not (Test-Path -LiteralPath $condaExe)) {
        $miniforgeInstaller = Get-VerifiedPackage $Packages.Miniforge
        $process = Start-Process -FilePath $miniforgeInstaller -ArgumentList "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /NoRegistry=1 /S /D=$MiniforgeRoot" -Wait -PassThru
        if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $condaExe)) {
            throw "Miniforge installation failed with exit code $($process.ExitCode)."
        }
    } else {
        Write-InstallLog "Miniforge is already installed." Green
    }

    Write-Step "Creating the Cellpose Python environment"
    if (-not (Test-Path -LiteralPath $CellposePython)) {
        Invoke-Checked -FilePath $condaExe -Arguments @("create", "-y", "-n", "cellpose", "python=3.10", "pip", "tk") -Description "Create cellpose conda environment"
    } else {
        Write-InstallLog "The cellpose environment already exists; it will be verified and updated." Green
    }

    Write-Step "Installing PyTorch and Cellpose-SAM"
    $gpuNames = Get-NvidiaGpuNames
    $torchIndex = if ($gpuNames.Count -gt 0) { "https://download.pytorch.org/whl/cu128" } else { "https://download.pytorch.org/whl/cpu" }
    if ($gpuNames.Count -gt 0) {
        Write-InstallLog ("NVIDIA GPU detected: " + ($gpuNames -join ", ")) Green
        Write-InstallLog "Installing PyTorch CUDA 12.8 wheels; a separate CUDA Toolkit is not required." Green
    } else {
        Write-InstallLog "No NVIDIA GPU/driver was detected. Installing CPU PyTorch." Yellow
    }
    Invoke-Checked -FilePath $CellposePython -Arguments @(
        "-m", "pip", "install", "--upgrade", "pip"
    ) -Description "Upgrade pip"
    Invoke-Checked -FilePath $CellposePython -Arguments @(
        "-m", "pip", "install", "torch==2.11.0", "torchvision==0.26.0", "--index-url", $torchIndex
    ) -Description "Install PyTorch"
    Invoke-Checked -FilePath $CellposePython -Arguments @(
        "-m", "pip", "install", "cellpose==4.2.1.1", "scikit-image==0.25.2", "pillow==10.3.0"
    ) -Description "Install Cellpose-SAM and scikit-image"
    if (-not $SkipModelDownload) {
        Invoke-Checked -FilePath $CellposePython -Arguments @(
            "-c", "from cellpose import models; models.CellposeModel(gpu=False, pretrained_model='cpsam'); print('cpsam model ready')"
        ) -Description "Download and validate the Cellpose-SAM cpsam model"
    }

    Write-Step "Installing QuPath 0.7.0"
    if (-not (Test-Path -LiteralPath $QuPathConsole)) {
        $qupathArchive = Get-VerifiedPackage $Packages.QuPath
        $qupathStage = Join-Path $CacheRoot "qupath-expand"
        if (Test-Path -LiteralPath $qupathStage) {
            Remove-Item -LiteralPath $qupathStage -Recurse -Force
        }
        New-Item -ItemType Directory -Path $qupathStage -Force | Out-Null
        Expand-Archive -LiteralPath $qupathArchive -DestinationPath $qupathStage -Force
        $consoleCandidate = Get-ChildItem -LiteralPath $qupathStage -Recurse -File -Filter "QuPath-0.7.0 (console).exe" | Select-Object -First 1
        if ($null -eq $consoleCandidate) {
            throw "The QuPath archive did not contain the expected console executable."
        }
        if (Test-Path -LiteralPath $QuPathRoot) {
            $backup = "$QuPathRoot.partial-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Move-Item -LiteralPath $QuPathRoot -Destination $backup
            Write-InstallLog "Moved an incomplete QuPath folder to $backup" Yellow
        }
        New-Item -ItemType Directory -Path $QuPathRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $consoleCandidate.Directory.FullName "*") -Destination $QuPathRoot -Recurse -Force
        if (-not (Test-Path -LiteralPath $QuPathConsole)) {
            throw "QuPath extraction failed."
        }
    } else {
        Write-InstallLog "QuPath 0.7.0 is already installed." Green
    }

    Write-Step "Installing QuPath ABBA and Cellpose extensions"
    $extensionRoot = Join-Path $env:USERPROFILE "QuPath\v0.7\extensions\catalogs"
    $catalogRoot = Join-Path $extensionRoot "QuPath-BIOP catalog"
    $cellposeExtensionRoot = Join-Path $catalogRoot "QuPath Cellpose extension\v0.12.1\main-jar"
    $abbaExtensionRoot = Join-Path $catalogRoot "QuPath ABBA extension\v0.5.0\main-jar"
    $abbaDependenciesRoot = Join-Path $catalogRoot "QuPath ABBA extension\v0.5.0\required-dependencies"
    New-Item -ItemType Directory -Path $cellposeExtensionRoot, $abbaExtensionRoot, $abbaDependenciesRoot -Force | Out-Null
    $cellposeExtension = Get-VerifiedPackage $Packages.CellposeExtension
    Expand-Archive -LiteralPath $cellposeExtension -DestinationPath $cellposeExtensionRoot -Force
    Copy-Item -LiteralPath $cellposeExtension -Destination $cellposeExtensionRoot -Force
    Copy-Item -LiteralPath (Get-VerifiedPackage $Packages.AbbaExtension) -Destination $abbaExtensionRoot -Force
    Copy-Item -LiteralPath (Get-VerifiedPackage $Packages.WarpyExtension) -Destination $abbaDependenciesRoot -Force
    New-Item -ItemType Directory -Path $extensionRoot -Force | Out-Null
    Update-QuPathExtensionRegistry -RegistryPath (Join-Path $extensionRoot "registry.json")

    Write-Step "Installing Fiji, ABBA 0.11.0, DeepSlice Local, and Elastix"
    $abbaReady = (Test-Path -LiteralPath (Join-Path $AbbaRoot "python.exe")) -and
        ($null -ne (Get-ChildItem -LiteralPath $AbbaRoot -Recurse -File -Filter "ImageJ-win64.exe" -ErrorAction SilentlyContinue | Select-Object -First 1))
    if (-not $abbaReady) {
        $abbaInstaller = Get-VerifiedPackage $Packages.Abba
        $abbaProcess = Start-Process -FilePath $abbaInstaller -ArgumentList "/S /D=$AbbaRoot" -Wait -PassThru
        if ($abbaProcess.ExitCode -ne 0) {
            throw "ABBA installation failed with exit code $($abbaProcess.ExitCode)."
        }
        $abbaReady = (Test-Path -LiteralPath (Join-Path $AbbaRoot "python.exe")) -or
            ($null -ne (Get-ChildItem -LiteralPath $AbbaRoot -Recurse -File -Filter "ImageJ-win64.exe" -ErrorAction SilentlyContinue | Select-Object -First 1))
        if (-not $abbaReady) {
            throw "ABBA installer returned successfully, but its installed files were not found in $AbbaRoot."
        }
    } else {
        Write-InstallLog "ABBA/Fiji is already installed." Green
    }

    Write-Step "Configuring Brain Cell Counter and shortcuts"
    Write-InitialSettings
    $launcherPath = Join-Path $InstallRoot "Launch_Brain_Cell_Counter.cmd"
    $launcherLines = @(
        "@echo off",
        "cd /d `"$InstallRoot`"",
        "start `"Brain Cell Counter v2.0.0`" `"$($CellposePython.Replace('python.exe', 'pythonw.exe'))`" `"$InstallRoot\run_brain_cell_counter_v1.0.py`""
    )
    Set-Content -LiteralPath $launcherPath -Value $launcherLines -Encoding ASCII
    $verifyLauncher = Join-Path $InstallRoot "Verify_Installation.cmd"
    Set-Content -LiteralPath $verifyLauncher -Encoding ASCII -Value @(
        "@echo off",
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallRoot\Verify_Brain_Cell_Counter_v1.0.ps1`"",
        "pause"
    )
    $desktop = [Environment]::GetFolderPath("Desktop")
    New-Shortcut -Path (Join-Path $desktop "Brain Cell Counter.lnk") -TargetPath $launcherPath -WorkingDirectory $InstallRoot -IconLocation "$env:SystemRoot\System32\shell32.dll,14"
    New-Shortcut -Path (Join-Path $desktop "QuPath 0.7.0.lnk") -TargetPath $QuPathConsole -WorkingDirectory $QuPathRoot -IconLocation "$QuPathConsole,0"

    Write-Step "Verifying the complete installation"
    $verifyScript = Join-Path $InstallRoot "Verify_Brain_Cell_Counter_v1.0.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyScript -NonInteractive
    if ($LASTEXITCODE -ne 0) {
        throw "Final verification failed. See $InstallRoot\Installation_Report.txt."
    }

    Write-Step "Installation complete"
    Write-InstallLog "$ProductName was installed successfully." Green
    Write-InstallLog "Desktop shortcut: Brain Cell Counter (v2.0.0)" Green
    Write-InstallLog "Installation report: $InstallRoot\Installation_Report.txt" Green
    Write-Host ""
    Write-Host "IMPORTANT:" -ForegroundColor Yellow
    Write-Host "1. The first ABBA launch may download additional Fiji components and atlases." -ForegroundColor Yellow
    Write-Host "2. An NVIDIA display driver is required for CUDA; the CUDA Toolkit itself is not required." -ForegroundColor Yellow
    Write-Host "3. Choose your project.qpproj in Brain Cell Counter, then run PREFLIGHT and VALIDATE." -ForegroundColor Yellow
    exit 0
} catch {
    Write-InstallLog "INSTALLATION FAILED: $($_.Exception.Message)" Red
    Write-Host "Review the installation log: $LogPath" -ForegroundColor Yellow
    exit 1
}
