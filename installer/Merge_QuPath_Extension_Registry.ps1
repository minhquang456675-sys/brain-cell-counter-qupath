<#
.SYNOPSIS
Safely merges the BIOP extension catalog into QuPath's extension registry.

.DESCRIPTION
Creates a timestamped backup before changing an existing registry and avoids
adding duplicate catalog entries.
#>
function Update-QuPathExtensionRegistry {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$RegistryPath)

    $registryDirectory = Split-Path -Parent $RegistryPath
    New-Item -ItemType Directory -Path $registryDirectory -Force | Out-Null
    $catalogs = @()
    if (Test-Path -LiteralPath $RegistryPath) {
        $backupPath = "$RegistryPath.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item -LiteralPath $RegistryPath -Destination $backupPath -Force
        Write-Host "Backed up the existing QuPath extension registry: $backupPath" -ForegroundColor Yellow
        try {
            $existing = Get-Content -LiteralPath $RegistryPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($null -ne $existing.catalogs) {
                $catalogs = @($existing.catalogs)
            }
        } catch {
            Write-Host "The existing registry was invalid; the backup was preserved." -ForegroundColor Yellow
            $catalogs = @()
        }
    }

    $qupathCatalog = $catalogs | Where-Object { $_.name -eq "QuPath catalog" } | Select-Object -First 1
    if ($null -eq $qupathCatalog) {
        $catalogs += [pscustomobject][ordered]@{
            name = "QuPath catalog"
            description = "Extensions maintained by the QuPath team"
            uri = "https://github.com/qupath/qupath-catalog"
            rawUri = "https://raw.githubusercontent.com/qupath/qupath-catalog/refs/heads/main/catalog.json"
            deletable = $false
            extensions = @()
        }
    }

    $biopCatalog = $catalogs | Where-Object { $_.name -eq "QuPath-BIOP catalog" } | Select-Object -First 1
    if ($null -eq $biopCatalog) {
        $biopCatalog = [pscustomobject][ordered]@{
            name = "QuPath-BIOP catalog"
            description = "Extensions maintained by the BIOP team"
            uri = "https://github.com/BIOP/qupath-biop-catalog"
            rawUri = "https://raw.githubusercontent.com/BIOP/qupath-biop-catalog/main/catalog.json"
            deletable = $true
            extensions = @()
        }
        $catalogs += $biopCatalog
    }
    if ($null -eq $biopCatalog.PSObject.Properties["extensions"]) {
        $biopCatalog | Add-Member -MemberType NoteProperty -Name extensions -Value @()
    }
    $extensions = @($biopCatalog.extensions)
    $requiredExtensions = @(
        [pscustomobject][ordered]@{
            name = "QuPath Cellpose extension"
            installedVersion = "v0.12.1"
            optionalDependenciesInstalled = $false
        },
        [pscustomobject][ordered]@{
            name = "QuPath ABBA extension"
            installedVersion = "v0.5.0"
            optionalDependenciesInstalled = $false
        }
    )
    foreach ($required in $requiredExtensions) {
        $installed = $extensions | Where-Object { $_.name -eq $required.name } | Select-Object -First 1
        if ($null -eq $installed) {
            $extensions += $required
            continue
        }
        $installed | Add-Member -MemberType NoteProperty -Name installedVersion -Value $required.installedVersion -Force
        $installed | Add-Member -MemberType NoteProperty -Name optionalDependenciesInstalled -Value $false -Force
    }
    $biopCatalog.extensions = $extensions

    $temporaryPath = "$RegistryPath.tmp"
    [ordered]@{ catalogs = @($catalogs) } |
        ConvertTo-Json -Depth 12 |
        Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $RegistryPath -Force
}
