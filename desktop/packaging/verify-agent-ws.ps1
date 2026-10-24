# Verify that the frozen LinkNote.exe really bundles `websockets` and opens the
# Agent WebSocket to the relay (regression: 0.2.1 shipped without websockets).
#
# Mode A (default): run the exe as the Agent worker with a prepared agent.json
#   powershell -File desktop\packaging\verify-agent-ws.ps1 `
#       -Exe dist-desktop\LinkNote\LinkNote.exe -Config desktop\build\ws-verify\agent.json
#
# Mode B (-AppLaunch): start the real desktop app with an isolated %LOCALAPPDATA%
# profile that already contains a paired agent.json; the app spawns
# `LinkNote.exe --agent-worker ... run` itself, exactly like a user install:
#   powershell -File desktop\packaging\verify-agent-ws.ps1 -AppLaunch `
#       -Exe dist-desktop\LinkNote\LinkNote.exe -Config desktop\build\ws-verify\agent.json
#
# PASS requires in the agent log:
#   * "收到 WebSocket hello" (or "实时连接已建立")
#   * no "No module named 'websockets'" anywhere in agent.log / agent-worker.log
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$Config,
    [int]$WaitSeconds = 15,
    [switch]$AppLaunch
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not (Test-Path $Exe)) { throw "exe not found: $Exe" }
if (-not (Test-Path $Config)) { throw "agent config not found: $Config" }
$cfg = Get-Content $Config -Raw | ConvertFrom-Json
$logDir = [string]$cfg.log_dir
if (-not $logDir) { throw "config has no log_dir" }
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$agentLog = Join-Path $logDir 'agent.log'
$workerLog = Join-Path $logDir 'agent-worker.log'
foreach ($p in @($agentLog, $workerLog)) { if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue } }

function Stop-Agent([int]$processId) {
    if ($processId -gt 0) {
        try { Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue } catch { }
    }
    foreach ($p in @(Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue)) {
        try {
            if ($p.Path -and $p.Path -like "*$([System.IO.Path]::GetFileNameWithoutExtension('') )*") { }
        } catch { }
    }
}

$proc = $null
$appProc = $null
$oldLocalAppData = $env:LOCALAPPDATA
try {
    if ($AppLaunch) {
        # Build an isolated profile whose agent.json is our paired config, then start
        # the app; the app spawns its own --agent-worker child.
        $profile = Join-Path (Split-Path $Config -Parent) 'app-profile'
        Remove-Item $profile -Recurse -Force -ErrorAction SilentlyContinue
        $profileLinkNote = Join-Path $profile 'LinkNote'
        New-Item -ItemType Directory -Force -Path $profileLinkNote | Out-Null
        $profileLogDir = Join-Path $profileLinkNote 'logs'
        $profileAgent = Get-Content $Config -Raw | ConvertFrom-Json
        $profileAgent.log_dir = $profileLogDir
        $profileAgent.outbox_dir = Join-Path $profileLinkNote 'outbox'
        $profileAgent.notes_dir = Join-Path $profileLinkNote 'notes'
        $profileAgent | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $profileLinkNote 'agent.json') -Encoding utf8
        # onboarding_done=true: the real app only starts engine/Agent after the
        # first-run wizard completed (that is the state of an existing user).
        @{ engine_auto_start = $false; close_to_tray = $false; start_minimized = $false; onboarding_done = $true } |
            ConvertTo-Json | Set-Content (Join-Path $profileLinkNote 'settings.json') -Encoding utf8
        $env:LOCALAPPDATA = $profile
        $appProc = Start-Process -FilePath $Exe -PassThru
        Write-Host "  app started pid=$($appProc.Id), waiting $WaitSeconds s for agent worker ..."
        $agentLog = Join-Path $profileLogDir 'agent.log'
        $workerLog = Join-Path $profileLogDir 'agent-worker.log'
    } else {
        $stdout = Join-Path $logDir 'agent-worker.log'
        $proc = Start-Process -FilePath $Exe -PassThru -NoNewWindow `
            -ArgumentList @('--agent-worker', '--config', "`"$Config`"", 'run') `
            -RedirectStandardOutput $stdout -RedirectStandardError ($stdout + '.err')
        Write-Host "  agent-worker started pid=$($proc.Id), waiting $WaitSeconds s ..."
    }
    Start-Sleep -Seconds $WaitSeconds
    $alive = $true
    if ($proc) { $proc.Refresh(); $alive = (-not $proc.HasExited) }
    if ($appProc) { $appProc.Refresh(); $alive = (-not $appProc.HasExited) }
} finally {
    if ($AppLaunch) {
        # stop the app and any agent child it spawned
        foreach ($p in @(Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue)) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
        $env:LOCALAPPDATA = $oldLocalAppData
    } else {
        if ($proc -and -not $proc.HasExited) {
            foreach ($p in @(Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue)) {
                try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch { }
            }
        }
    }
}

$agentLogText = if (Test-Path $agentLog) { Get-Content $agentLog -Raw } else { '' }
$workerLogText = if (Test-Path $workerLog) { Get-Content $workerLog -Raw } else { '' }
$workerErrText = if (Test-Path ($workerLog + '.err')) { Get-Content ($workerLog + '.err') -Raw } else { '' }
$allText = "$agentLogText`n$workerLogText`n$workerErrText"

$helloSeen = ($agentLogText -match '收到 WebSocket hello') -or ($agentLogText -match '实时连接已建立')
$wsAttempt = ($allText -match 'wss?://[^\s]+/api/v1/agent/ws') -or ($allText -match 'WebSocket')
$missingWebsockets = ($allText -match 'No module named .websockets')
$result = [ordered]@{
    exe                    = $Exe
    mode                   = if ($AppLaunch) { 'app-launch' } else { 'agent-worker' }
    waited_seconds         = $WaitSeconds
    process_alive_at_end   = $alive
    agent_log              = $agentLog
    worker_log             = $workerLog
    ws_attempt_seen        = $wsAttempt
    ws_hello_seen          = $helloSeen
    missing_websockets     = $missingWebsockets
    agent_log_tail         = @($agentLogText -split "`r?`n" | Select-Object -Last 12)
    worker_log_tail        = @(("$workerLogText`n$workerErrText") -split "`r?`n" | Select-Object -Last 8)
    verified               = ($helloSeen -and -not $missingWebsockets)
}
$result | ConvertTo-Json -Depth 5
if ($result.verified) { Write-Host 'AGENT WS VERIFY PASS' -ForegroundColor Green; exit 0 }
Write-Host 'AGENT WS VERIFY FAIL' -ForegroundColor Red
exit 1

