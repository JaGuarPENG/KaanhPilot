@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B -u launcher.py
    goto end
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -B -u launcher.py
    goto end
)
where python >nul 2>nul
if not errorlevel 1 (
    python -B -u launcher.py
    goto end
)
echo 找不到 Python，请先安装 Python 3.10+ 或使用原 KaanhOrbit 环境。
:end
pause
endlocal
