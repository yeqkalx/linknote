<#
.SYNOPSIS
  Stage a clean BiliNote engine distribution for the LinkNote installer.

.DESCRIPTION
  Copies the BiliNoteBackend.exe onedir distribution from a local build
  (default: C:\app\bilinote) into desktop\build\engine, keeping ONLY the files
  needed to run the engine:

      BiliNoteBackend.exe
      _internal\        (PyInstaller runtime, ~270 MB)
      static\           (runtime output dir, screenshots)

  Deliberately NOT copied (user data / packaging leftovers):
      config\ data\ note_results\ logs\ uploads\ vector_db\
      bili_note.db  Uninstall BiliNote.lnk  app.exe (Tauri shell)

  The staged tree is the payload of the optional "bundled BiliNote engine"
  installer component; it is gitignored and must not be committed.

  Output: desktop\build\engine  +  desktop\build\engine-manifest.json

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File desktop\packaging\stage-engine.ps1
  powershell -ExecutionPolicy Bypass -File desktop\packaging\stage-engine.ps1 -Source C:\app\bilinote -Force
#>
[CmdletBinding()]
param(
    [string]$Source = 'C:\app\bilinote',
    [string]$Dest = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $PSScriptRoot) { $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
$DesktopDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Dest) { $Dest = Join-Path $DesktopDir 'build\engine' }
$Manifest = Join-Path $DesktopDir 'build\engine-manifest.json'

function Step([string]$m) { Write-Host ''; Write-Host "=== $m ===" -ForegroundColor Cyan }
function Info([string]$m) { Write-Host "  $m" }
function Fail([string]$m) { Write-Host "  [error] $m" -ForegroundColor Red; exit 1 }

$IncludeItems = @('BiliNoteBackend.exe', '_internal', 'static')
$ExcludedItems = @(
    'config', 'data', 'note_results', 'logs', 'uploads', 'vector_db',
    'bili_note.db', 'Uninstall BiliNote.lnk', 'app.exe'
)

Step '0/3 Validate source / destination'
if (-not (Test-Path $Source)) { Fail "source not found: $Source" }
$Source = (Resolve-Path $Source).Path
$engineExe = Join-Path $Source 'BiliNoteBackend.exe'
if (-not (Test-Path $engineExe)) { Fail "BiliNoteBackend.exe not found under $Source" }
Info "source: $Source"

# Refuse to build the staged tree if any excluded user-data item was ever put on the include list.
foreach ($x in $ExcludedItems) {
    if ($IncludeItems -contains $x) { Fail "refusing to stage excluded item: $x" }
}

# Safety: only ever delete desktop\build\engine (never a caller-supplied absolute path outside build/).
$buildRoot = (Join-Path $DesktopDir 'build')
if (-not (Test-Path $buildRoot)) { New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null }
$buildRootFull = (Resolve-Path $buildRoot).Path
$destFull = [System.IO.Path]::GetFullPath($Dest)
if (-not $destFull.StartsWith($buildRootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    Fail "destination must live under $buildRootFull (got $destFull)"
}
if (Test-Path $destFull) {
    if ($Force -or $destFull -eq (Join-Path $buildRootFull 'engine')) {
        Info "cleaning existing staging dir: $destFull"
        Remove-Item -LiteralPath $destFull -Recurse -Force
    } else {
        Fail "destination exists (pass -Force to replace): $destFull"
    }
}
New-Item -ItemType Directory -Force -Path $destFull | Out-Null
Info "dest:   $destFull"

Step '1/3 Copy engine payload'
function Copy-RobocopyTree([string]$srcDir, [string]$dstDir, [string]$label) {
    if (-not (Test-Path $srcDir)) { Fail "expected payload dir missing: $srcDir" }
    # cmd wrapper: robocopy logs to stderr, which would trip $ErrorActionPreference='Stop'
    cmd /c "robocopy `"$srcDir`" `"$dstDir`" /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP 2>&1" | Select-Object -Last 1 | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) { Fail "robocopy failed for $label (exit $rc)" }
    Info "copied: $label (robocopy exit $rc)"
}
# engine exe: single file copy (robocopy with /E would happily copy the whole source tree)
$srcExe = Join-Path $Source 'BiliNoteBackend.exe'
Copy-Item -LiteralPath $srcExe -Destination (Join-Path $destFull 'BiliNoteBackend.exe') -Force
Info "copied: BiliNoteBackend.exe ($([math]::Round((Get-Item $srcExe).Length/1MB,1)) MB)"
Copy-RobocopyTree (Join-Path $Source '_internal') (Join-Path $destFull '_internal') '_internal'
Copy-RobocopyTree (Join-Path $Source 'static') (Join-Path $destFull 'static') 'static'

Step '2/3 Validate staging is clean'
foreach ($bad in @('config', 'data', 'note_results', 'logs', 'uploads', 'vector_db', 'bili_note.db', 'app.exe')) {
    if (Test-Path (Join-Path $destFull $bad)) { Fail "staging contains excluded item: $bad" }
}
$strayDb = Get-ChildItem $destFull -Recurse -File -Filter 'bili_note.db' -ErrorAction SilentlyContinue
if ($strayDb) { Fail "staging contains bili_note.db: $($strayDb[0].FullName)" }
Info 'no user data / db / Tauri shell present'

Step '3/3 Manifest + size report'
$files = Get-ChildItem $destFull -Recurse -File
$totalBytes = ($files | Measure-Object Length -Sum).Sum
$sha = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $destFull 'BiliNoteBackend.exe')).Hash
$manifestObj = [ordered]@{
    staged_at          = (Get-Date).ToString('o')
    source             = $Source
    destination        = $destFull
    included           = $IncludeItems
    excluded           = $ExcludedItems
    file_count         = $files.Count
    total_bytes        = [int64]$totalBytes
    total_mb           = [math]::Round($totalBytes / 1MB, 1)
    engine_exe         = 'BiliNoteBackend.exe'
    engine_exe_bytes   = (Get-Item (Join-Path $destFull 'BiliNoteBackend.exe')).Length
    engine_exe_sha256  = $sha
    license            = 'BiliNote (MIT) - see LICENSE / NOTICE in the project root'
}
$manifestObj | ConvertTo-Json -Depth 5 | Set-Content -Path $Manifest -Encoding utf8

Write-Host ''
Write-Host 'Engine staged.' -ForegroundColor Green
Write-Host ("  {0} files, {1:N1} MB -> {2}" -f $files.Count, ($totalBytes / 1MB), $destFull)
Write-Host "  BiliNoteBackend.exe SHA256: $sha"
Write-Host "  manifest: $Manifest"
Write-Host '  (the engine is NOT started by this script; port 8483 is left untouched)'
