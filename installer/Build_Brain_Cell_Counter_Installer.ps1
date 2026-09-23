<#
.SYNOPSIS
Builds the optional self-extracting Windows installer executable.

.DESCRIPTION
Stages the same source payload as the modular installer and packages it with
IExpress. Generated files are never edited directly.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$InstallerRoot = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $InstallerRoot
$BuildRoot = Join-Path ([IO.Path]::GetTempPath()) "BCC-v1-installer-build"
$StageRoot = Join-Path $BuildRoot "payload"
$DistRoot = Join-Path $ProjectRoot "dist"
$PayloadZip = Join-Path $BuildRoot "payload.zip"
$OutputExe = Join-Path $DistRoot "Brain_Cell_Counter_v2.4.1_Setup.exe"
$TemporaryOutputExe = Join-Path ([IO.Path]::GetTempPath()) "BCC-v1-setup-build.exe"
$SedPath = Join-Path $BuildRoot "Brain_Cell_Counter_v2.4.1_Setup.sed"

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "run_brain_cell_counter_v1.0.py"))) {
    throw "Project root was not detected correctly: $ProjectRoot"
}
if (Test-Path -LiteralPath $BuildRoot) {
    $resolvedBuild = [IO.Path]::GetFullPath($BuildRoot)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolvedBuild.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clear a build directory outside the Windows temporary folder."
    }
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $StageRoot, $DistRoot -Force | Out-Null
if (Test-Path -LiteralPath $TemporaryOutputExe) {
    Remove-Item -LiteralPath $TemporaryOutputExe -Force
}

Copy-Item -LiteralPath (Join-Path $ProjectRoot "run_brain_cell_counter_v1.0.py") -Destination $StageRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "README.md") -Destination $StageRoot
Copy-Item -LiteralPath (Join-Path $ProjectRoot "CHANGELOG.md") -Destination $StageRoot
Copy-Item -LiteralPath (Join-Path $InstallerRoot "Verify_Brain_Cell_Counter_v1.0.ps1") -Destination $StageRoot
Copy-Item -LiteralPath (Join-Path $InstallerRoot "THIRD_PARTY_NOTICES.md") -Destination $StageRoot
$scriptStage = Join-Path $StageRoot "qupath_scripts"
New-Item -ItemType Directory -Path $scriptStage -Force | Out-Null
Copy-Item -Path (Join-Path $ProjectRoot "qupath_scripts\*.groovy") -Destination $scriptStage

$packageStage = Join-Path $StageRoot "brain_cell_counter"
New-Item -ItemType Directory -Path $packageStage -Force | Out-Null
foreach ($name in @("__init__.py", "version.py", "app_core.py", "qt_app.py", "qt_results.py", "qt_calibration.py", "hybrid_app.py", "threshold_optimizer.py", "calibration.py", "calibration_wizard.py", "calibration_runner.py", "results_preview.py")) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "brain_cell_counter\$name") -Destination $packageStage
}

$manifest = Get-Content -LiteralPath (Join-Path $InstallerRoot "installer_manifest.json") -Raw | ConvertFrom-Json
$manifest.build_timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $StageRoot "installer_manifest.json") -Encoding UTF8

Compress-Archive -Path (Join-Path $StageRoot "*") -DestinationPath $PayloadZip -CompressionLevel Optimal
Copy-Item -LiteralPath (Join-Path $InstallerRoot "Install_Brain_Cell_Counter_v1.0.ps1") -Destination $BuildRoot
Copy-Item -LiteralPath (Join-Path $InstallerRoot "Merge_QuPath_Extension_Registry.ps1") -Destination $BuildRoot
Copy-Item -LiteralPath (Join-Path $InstallerRoot "setup.cmd") -Destination $BuildRoot

$sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt=Install Brain Cell Counter v2.4.1 and its scientific software stack?
DisplayLicense=
FinishMessage=
TargetName=$TemporaryOutputExe
FriendlyName=Brain Cell Counter v2.4.1 Setup
AppLaunched=cmd /c setup.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="payload.zip"
FILE1="Install_Brain_Cell_Counter_v1.0.ps1"
FILE2="setup.cmd"
FILE3="Merge_QuPath_Extension_Registry.ps1"
[SourceFiles]
SourceFiles0=$BuildRoot\
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
%FILE3%=
"@
Set-Content -LiteralPath $SedPath -Value $sed -Encoding ASCII

$iexpress = Join-Path $env:SystemRoot "System32\iexpress.exe"
$process = Start-Process -FilePath $iexpress -ArgumentList "/N `"$SedPath`"" -Wait -PassThru
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $TemporaryOutputExe)) {
    throw "IExpress failed to build the setup executable. Exit code: $($process.ExitCode)"
}
Copy-Item -LiteralPath $TemporaryOutputExe -Destination $OutputExe -Force

$hash = (Get-FileHash -LiteralPath $OutputExe -Algorithm SHA256).Hash.ToLowerInvariant()
$sizeMb = [Math]::Round((Get-Item -LiteralPath $OutputExe).Length / 1MB, 2)
Set-Content -LiteralPath (Join-Path $DistRoot "Brain_Cell_Counter_v2.4.1_Setup.sha256.txt") -Encoding ASCII -Value "$hash  Brain_Cell_Counter_v2.4.1_Setup.exe"
Write-Host "Built: $OutputExe"
Write-Host "Size: $sizeMb MB"
Write-Host "SHA-256: $hash"
