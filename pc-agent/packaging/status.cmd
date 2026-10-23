@echo off
chcp 65001 >nul
rem 查看 LinkNote PC Agent 状态：配对、Relay 在线、BiliNote 后端、待补传笔记
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 找不到虚拟环境 .venv\Scripts\python.exe
    echo        请先在本目录执行 uv venv .venv --python 3.11 和 uv pip install ...
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m linknote_agent status %*
pause
