<#
.SYNOPSIS
Builds a portable Codex handoff containing source, Git history, and installer.

.DESCRIPTION
Creates generated artifacts only under dist/. Machine-specific settings,
scientific images, QuPath projects, and result folders are never included.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$InstallerRoot = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $InstallerRoot
$DistRoot = Join-Path $ProjectRoot "dist"

$versionSource = Get-Content -LiteralPath (Join-Path $ProjectRoot "brain_cell_counter\version.py") -Raw
if ($versionSource -notmatch 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
    throw "Could not read APP_VERSION from brain_cell_counter/version.py."
}
$version = $Matches[1]
$packageName = "Brain_Cell_Counter_Codex_Transfer_v$version"
$stage = Join-Path $DistRoot $packageName
$packageZip = Join-Path $DistRoot "$packageName.zip"
$packageHash = Join-Path $DistRoot "$packageName.sha256.txt"
$installerZip = Join-Path $DistRoot "Brain_Cell_Counter_v${version}_Installer_Package.zip"

if (-not (Test-Path -LiteralPath $installerZip)) {
    & (Join-Path $InstallerRoot "Build_Modular_Installer_Package.ps1")
}
if (-not (Test-Path -LiteralPath $installerZip)) {
    throw "The version-matched installer package was not built: $installerZip"
}

New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
if (Test-Path -LiteralPath $stage) {
    $resolvedStage = [IO.Path]::GetFullPath($stage)
    $resolvedDist = [IO.Path]::GetFullPath($DistRoot)
    if (-not $resolvedStage.StartsWith($resolvedDist, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clear an unexpected staging path: $stage"
    }
    Remove-Item -LiteralPath $stage -Recurse -Force
}
foreach ($path in @($packageZip, $packageHash)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force
    }
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$sourceZip = Join-Path $stage "Brain_Cell_Counter_Source_v$version.zip"
$historyBundle = Join-Path $stage "Brain_Cell_Counter_History_v$version.bundle"
$installerCopy = Join-Path $stage (Split-Path -Leaf $installerZip)
$guide = Join-Path $stage "README_CODEX_TRANSFER.md"

Push-Location $ProjectRoot
try {
    $commit = (git rev-parse HEAD).Trim()
    $branch = (git branch --show-current).Trim()
    if (-not $commit) { throw "Could not determine the current Git commit." }
    git archive --format=zip --output=$sourceZip HEAD
    if ($LASTEXITCODE -ne 0) { throw "git archive failed." }
    # Export project branches and tags only; omit temporary refs created by
    # development tools such as Codex turn-diff checkpoints.
    git bundle create $historyBundle --branches --tags
    if ($LASTEXITCODE -ne 0) { throw "git bundle failed." }
} finally {
    Pop-Location
}

Copy-Item -LiteralPath $installerZip -Destination $installerCopy
Copy-Item -LiteralPath (Join-Path $ProjectRoot "docs\CODEX_TRANSFER.md") -Destination $guide

$manifest = [ordered]@{
    product = "Brain Cell Counter"
    application_version = $version
    git_commit = $commit
    git_branch = $branch
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    excludes = @(
        "brain_cell_counter_settings.json",
        "microscopy images",
        "QuPath projects",
        "generated results",
        "credentials and caches"
    )
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stage "TRANSFER_MANIFEST.json") -Encoding UTF8

$artifactPaths = @($sourceZip, $historyBundle, $installerCopy)
$checksumLines = foreach ($artifact in $artifactPaths) {
    $hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $(Split-Path -Leaf $artifact)"
}
Set-Content -LiteralPath (Join-Path $stage "SHA256SUMS.txt") -Value $checksumLines -Encoding ASCII

Compress-Archive -Path $stage -DestinationPath $packageZip -CompressionLevel Optimal
$finalHash = (Get-FileHash -LiteralPath $packageZip -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $packageHash -Value "$finalHash  $(Split-Path -Leaf $packageZip)" -Encoding ASCII

Write-Host "Built transfer folder: $stage"
Write-Host "Built transfer package: $packageZip"
Write-Host "SHA-256: $finalHash"
