# MSI database inspector based on the WiX DTF assembly (Microsoft.Deployment.WindowsInstaller).
# Works from Windows PowerShell 5.1 (.NET Framework), which can load DTF.
# Usage:
#   powershell -File desktop\packaging\msi\inspect-msi.ps1 -Table Property
#   powershell -File desktop\packaging\msi\inspect-msi.ps1 -Query "SELECT * FROM Property WHERE Property LIKE 'WIXUI%'"
#   powershell -File desktop\packaging\msi\inspect-msi.ps1 -Table Control -Query "SELECT * FROM Control WHERE Dialog_='ExitDialog'"
#   powershell -File desktop\packaging\msi\inspect-msi.ps1 -Table Summary
[CmdletBinding()]
param(
    [string]$Msi = '',
    [string]$Table = 'Property',
    [string]$Query = '',
    [int]$MaxRows = 500
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $Msi) { $Msi = Join-Path $repo 'desktop\build\msi\LinkNote-Setup-0.2.0.msi' }
if (-not (Test-Path $Msi)) { throw "MSI not found: $Msi" }

$dtf = Join-Path $repo 'tools\wix314\Microsoft.Deployment.WindowsInstaller.dll'
Add-Type -Path $dtf

$db = New-Object Microsoft.Deployment.WindowsInstaller.Database($Msi, [Microsoft.Deployment.WindowsInstaller.DatabaseOpenMode]::ReadOnly)
Write-Host ("MSI: {0}" -f $Msi)

if ($Table -eq 'Summary') {
    $si = $db.SummaryInfo
    Write-Host ("Title: {0}" -f $si.Title)
    Write-Host ("Subject: {0}" -f $si.Subject)
    Write-Host ("Author: {0}" -f $si.Author)
    Write-Host ("WordCount(InstallScope?): {0}" -f $si.WordCount)
    Write-Host ("PageCount: {0}" -f $si.PageCount)
    $db.Close()
    exit 0
}

if (-not $Query) { $Query = "SELECT * FROM $Table" }
$columns = @()
try {
    $tableInfo = $db.Tables[$Table]
    if ($tableInfo) { $columns = @($tableInfo.Columns | ForEach-Object { $_.Name }) }
} catch { }

$rows = @($db.ExecuteQuery($Query))
$out = New-Object System.Collections.ArrayList
foreach ($rec in $rows) {
    $row = [ordered]@{}
    for ($i = 1; $i -le $rec.FieldCount; $i++) {
        $name = if ($columns.Count -ge $i) { $columns[$i - 1] } else { "Col$i" }
        $val = $null
        try { $val = $rec.GetString($i) } catch { $val = $rec.GetInteger($i) }
        $row[$name] = $val
    }
    [void]$out.Add([pscustomobject]$row)
    if ($out.Count -ge $MaxRows) { break }
}
$out | Format-Table -AutoSize -Wrap
Write-Host ("rows: {0}  (query: {1})" -f $out.Count, $Query)
$db.Close()
