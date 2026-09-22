<#
.SYNOPSIS
Installs the pinned QuPath release and required BIOP extensions.

.DESCRIPTION
Downloads official packages into a resumable cache, verifies SHA-256 hashes,
and then installs QuPath, Cellpose, ABBA, and Warpy integration components.
#>
[CmdletBinding()]
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$QuPathRoot = Join-Path $env:LOCALAPPDATA "QuPath-0.7.0"
$QuPathConsole = Join-Path $QuPathRoot "QuPath-0.7.0 (console).exe"
$CacheRoot = Join-Path $env:LOCALAPPDATA "BrainCellCounter\installer-cache"
. (Join-Path $PSScriptRoot "Merge_QuPath_Extension_Registry.ps1")

$Packages = @(
    @{ Name="QuPath-v0.7.0-Windows.zip"; Url="https://github.com/qupath/qupath/releases/download/v0.7.0/QuPath-v0.7.0-Windows.zip"; Sha="5839743281d11eca1ac55c87ddcf92c89a30cba7ba2060f162036b0514b1e30c" },
    @{ Name="qupath-extension-cellpose-0.12.1.zip"; Url="https://github.com/BIOP/qupath-extension-cellpose/releases/download/v0.12.1/qupath-extension-cellpose-0.12.1.zip"; Sha="9b31ccc616f07740a52d1e4f0c6da83a14d1011b35b70b5ba847113b6fcb63da" },
    @{ Name="qupath-extension-abba-0.5.0.jar"; Url="https://github.com/BIOP/qupath-extension-abba/releases/download/0.5.0/qupath-extension-abba-0.5.0.jar"; Sha="0e27fc8486bfb20b917c787c4bd5d8463aefd4f3ec0bf5bb160436af7a53b1d3" },
    @{ Name="qupath-extension-warpy-0.5.0.jar"; Url="https://github.com/BIOP/qupath-extension-warpy/releases/download/0.5.0/qupath-extension-warpy-0.5.0.jar"; Sha="e4bc1c636adbcfd6e6dafde32d615afa032d3c0a21458660b2de380a884f5cb8" }
)

function Get-Package([hashtable]$Package) {
    $destination = Join-Path $CacheRoot $Package.Name
    if (Test-Path -LiteralPath $destination) {
        $hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -eq $Package.Sha) { Write-Host "Using cached $($Package.Name)" -ForegroundColor Green; return $destination }
        Remove-Item -LiteralPath $destination -Force
    }
    $partial = "$destination.part"
    Write-Host "Downloading $($Package.Name)..." -ForegroundColor Yellow
    & "$env:SystemRoot\System32\curl.exe" -L --fail --retry 5 --retry-delay 3 --continue-at - --output $partial $Package.Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $($Package.Name)" }
    Move-Item -LiteralPath $partial -Destination $destination -Force
    $hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $Package.Sha) { Remove-Item -LiteralPath $destination -Force; throw "SHA-256 mismatch: $($Package.Name)" }
    return $destination
}

try {
    Write-Host "QuPath 0.7.0 + BIOP Extensions Setup" -ForegroundColor Cyan
    if ($DryRun) { Write-Host "Dry run passed. No files were changed." -ForegroundColor Green; exit 0 }
    New-Item -ItemType Directory -Path $CacheRoot -Force | Out-Null
    $downloaded = @{}
    foreach ($package in $Packages) { $downloaded[$package.Name] = Get-Package $package }

    if (-not (Test-Path -LiteralPath $QuPathConsole)) {
        $stage = Join-Path $CacheRoot "qupath-expand"
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        Expand-Archive -LiteralPath $downloaded["QuPath-v0.7.0-Windows.zip"] -DestinationPath $stage -Force
        $console = Get-ChildItem -LiteralPath $stage -Recurse -File -Filter "QuPath-0.7.0 (console).exe" | Select-Object -First 1
        if ($null -eq $console) { throw "QuPath console executable was not found in the archive." }
        if (Test-Path -LiteralPath $QuPathRoot) {
            Move-Item -LiteralPath $QuPathRoot -Destination "$QuPathRoot.partial-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        }
        New-Item -ItemType Directory -Path $QuPathRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $console.Directory.FullName "*") -Destination $QuPathRoot -Recurse -Force
    }

    $catalogRoot = Join-Path $env:USERPROFILE "QuPath\v0.7\extensions\catalogs\QuPath-BIOP catalog"
    $cellposeRoot = Join-Path $catalogRoot "QuPath Cellpose extension\v0.12.1\main-jar"
    $abbaRoot = Join-Path $catalogRoot "QuPath ABBA extension\v0.5.0\main-jar"
    $warpyRoot = Join-Path $catalogRoot "QuPath ABBA extension\v0.5.0\required-dependencies"
    New-Item -ItemType Directory -Path $cellposeRoot, $abbaRoot, $warpyRoot -Force | Out-Null
    Expand-Archive -LiteralPath $downloaded["qupath-extension-cellpose-0.12.1.zip"] -DestinationPath $cellposeRoot -Force
    Copy-Item -LiteralPath $downloaded["qupath-extension-abba-0.5.0.jar"] -Destination $abbaRoot -Force
    Copy-Item -LiteralPath $downloaded["qupath-extension-warpy-0.5.0.jar"] -Destination $warpyRoot -Force
    $registryRoot = Join-Path $env:USERPROFILE "QuPath\v0.7\extensions\catalogs"
    Update-QuPathExtensionRegistry -RegistryPath (Join-Path $registryRoot "registry.json")

    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $desktop "QuPath 0.7.0.lnk"))
    $shortcut.TargetPath = $QuPathConsole
    $shortcut.WorkingDirectory = $QuPathRoot
    $shortcut.IconLocation = "$QuPathConsole,0"
    $shortcut.Save()
    Write-Host "QuPath and all required extensions were installed successfully." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "QuPath setup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
