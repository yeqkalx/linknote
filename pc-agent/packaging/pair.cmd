@echo off
chcp 65001 >nul
rem 创建配对：终端会显示 8 位配对码，手机输入后本脚本自动退出。
rem 用法：
rem   pair.cmd
rem   pair.cmd --relay http://127.0.0.1:8787     （本地开发 Relay）
rem   pair.cmd --name 我的书房电脑
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 找不到虚拟环境 .venv\Scripts\python.exe
    echo        请先在本目录执行：
    echo            uv venv .venv --python 3.11
    echo            uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m linknote_agent pair %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo [提示] 配对没有完成（退出码 %EXITCODE%）。可重试或检查 Relay 地址/网络。
)
pause
exit /b %EXITCODE%
