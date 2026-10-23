@echo off
chcp 65001 >nul
rem 启动 LinkNote PC Agent（常驻轮询 Relay 任务，调用本机 BiliNote）
rem 可传参数，例如：start_agent.cmd --verbose
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 找不到虚拟环境 .venv\Scripts\python.exe
    echo        请先在本目录执行：
    echo            uv venv .venv --python 3.11
    echo            uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m linknote_agent run %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo [提示] Agent 已退出，退出码 %EXITCODE%。日志见 %%USERPROFILE%%\.linknote\logs\agent.log
)
pause
exit /b %EXITCODE%
