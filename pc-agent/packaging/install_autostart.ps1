<#
.SYNOPSIS
    把 LinkNote PC Agent 注册成 Windows 登录自启（当前用户「启动」文件夹快捷方式）。

.DESCRIPTION
    - 用项目自带虚拟环境里的 pythonw.exe 静默运行（不弹黑窗口）；
    - 快捷方式放进当前用户的启动文件夹，登录后自动启动；
    - 数据/日志在 %USERPROFILE%\.linknote\ 下；
    - 不引用任何固定的项目绝对路径：脚本按自身位置推算仓库根目录。

.PARAMETER Remove
    移除已安装的启动快捷方式。

.PARAMETER StartupFolder
    覆盖启动文件夹路径（一般不需要，自动化测试可用）。

.PARAMETER Start
    安装后立刻在后台启动一次 Agent。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install_autostart.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Remove
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Start
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [string]$StartupFolder = "",
    [switch]$Start
)

$ErrorActionPreference = 'Stop'

$packagingDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $packagingDir
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'

if ($StartupFolder) {
    $startup = $StartupFolder
} else {
    $startup = [Environment]::GetFolderPath('Startup')
}
$shortcutPath = Join-Path $startup 'LinkNote Agent.lnk'

if ($Remove) {
    if (Test-Path $shortcutPath) {
        Remove-Item $shortcutPath -Force
        Write-Host "[OK] 已移除登录自启：$shortcutPath" -ForegroundColor Green
    } else {
        Write-Host "[INFO] 没有找到自启快捷方式：$shortcutPath" -ForegroundColor Yellow
    }
    return
}

if (-not (Test-Path $pythonw)) {
    Write-Host "[ERROR] 找不到虚拟环境里的 pythonw.exe：$pythonw" -ForegroundColor Red
    Write-Host "        请先在 pc-agent 目录执行：" -ForegroundColor Red
    Write-Host "            uv venv .venv --python 3.11" -ForegroundColor Yellow
    Write-Host '            uv pip install --python .venv\Scripts\python.exe -e ".[dev]"' -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $startup)) {
    New-Item -ItemType Directory -Path $startup -Force | Out-Null
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '-m linknote_agent run'
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7  # 7 = 最小化，实际 pythonw 无窗口
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
$shortcut.Description = 'LinkNote PC Agent：把手机发来的视频链接交给本机 BiliNote 生成笔记'
$shortcut.Save()

# 释放 COM 对象（PowerShell 里不显式释放也能用，这里简单清理）
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null

Write-Host "[OK] 已安装登录自启：$shortcutPath" -ForegroundColor Green
Write-Host "     程序：$pythonw -m linknote_agent run"
Write-Host "     工作目录：$root"
Write-Host "     日志：$env:USERPROFILE\.linknote\logs\agent.log"
Write-Host ""
Write-Host "立即后台启动一次：Start-Process '$pythonw' -ArgumentList '-m linknote_agent run' -WorkingDirectory '$root' -WindowStyle Hidden"
Write-Host "查看状态        ：双击 packaging\status.cmd（或 python -m linknote_agent status）"
Write-Host "取消自启        ：powershell -NoProfile -ExecutionPolicy Bypass -File `"$packagingDir\install_autostart.ps1`" -Remove"
Write-Host ""

if ($Start) {
    Start-Process -FilePath $pythonw `
        -ArgumentList '-m linknote_agent run' `
        -WorkingDirectory $root `
        -WindowStyle Hidden
    Write-Host "[OK] 已在后台启动 Agent（pythonw 无窗口）" -ForegroundColor Green
}
