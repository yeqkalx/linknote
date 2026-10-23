# 一键本地启动：同步 PWA → 初始化本地 D1（首次）→ 启动 Relay + PWA
# 用法：powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "==> 同步 PWA 到 relay/public" -ForegroundColor Cyan
npm --prefix app run sync
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$state = Join-Path $root "relay\.wrangler\state"
if (-not (Test-Path $state)) {
    Write-Host "==> 首次运行：应用本地 D1 迁移" -ForegroundColor Cyan
    npm --prefix relay run db:local
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "Relay + PWA: http://127.0.0.1:8787" -ForegroundColor Green
Write-Host "另开一个窗口启动 PC Agent：cd pc-agent; .venv\Scripts\python.exe -m linknote_agent run" -ForegroundColor Green
Write-Host ""
Set-Location (Join-Path $root "relay")
npm run dev
