# 安装依赖并初始化本地开发环境（Windows）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "==> 安装 Relay 依赖" -ForegroundColor Cyan
npm --prefix relay install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> 安装 PWA 依赖并同步" -ForegroundColor Cyan
npm --prefix app install
npm --prefix app run vendor
npm --prefix app run sync
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> 初始化本地 D1" -ForegroundColor Cyan
$env:CI = "1"
npm --prefix relay run db:local
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "未找到 uv，请先安装：https://docs.astral.sh/uv/" -ForegroundColor Red
    exit 1
}
Write-Host "==> 创建 PC Agent 虚拟环境" -ForegroundColor Cyan
Push-Location (Join-Path $root "pc-agent")
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { exit $code }

Write-Host ""
Write-Host "完成。下一步：" -ForegroundColor Green
Write-Host "  1) npm run dev                # 启动 Relay + PWA http://127.0.0.1:8787"
Write-Host "  2) cd pc-agent; .venv\Scripts\python.exe -m linknote_agent pair --relay http://127.0.0.1:8787"
Write-Host "  3) cd pc-agent; .venv\Scripts\python.exe -m linknote_agent run"
