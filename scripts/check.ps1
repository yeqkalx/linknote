# 开发自检：类型检查 + Relay 契约测试 + PC Agent 单测（如果已安装依赖）
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "==> Relay typecheck" -ForegroundColor Cyan
npm --prefix relay run typecheck
$relayType = $LASTEXITCODE

Write-Host "==> Relay contract e2e" -ForegroundColor Cyan
npm --prefix relay test
$relayTest = $LASTEXITCODE

$agentTest = 0
$py = Join-Path $root "pc-agent\.venv\Scripts\python.exe"
if (Test-Path $py) {
    Write-Host "==> PC Agent pytest" -ForegroundColor Cyan
    Push-Location (Join-Path $root "pc-agent")
    & $py -m pytest -q
    $agentTest = $LASTEXITCODE
    Pop-Location
} else {
    Write-Host "==> PC Agent：未找到 pc-agent\.venv，跳过（先按 README 安装）" -ForegroundColor Yellow
}

Write-Host ""
if ($relayType -eq 0 -and $relayTest -eq 0 -and $agentTest -eq 0) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "CHECK FAILED (type=$relayType relay=$relayTest agent=$agentTest)" -ForegroundColor Red
    exit 1
}
