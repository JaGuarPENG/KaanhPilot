@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "PYTHONUNBUFFERED=1"
set "PYTHONUTF8=1"
for %%F in (launcher.py launcher_config.json server.py device_adapter.py) do (
    if not exist "%%F" (
        echo Missing file: %%F. Restore the complete frontend folder.
        pause
        exit /b 1
    )
)
if exist ".venv\Scripts\python.exe" goto venv
where py >nul 2>&1
if not errorlevel 1 goto pylauncher
where python >nul 2>&1
if not errorlevel 1 goto python
 echo Python 3.10 or newer is required. Install Python or configure .venv.
pause
exit /b 1

:venv
".venv\Scripts\python.exe" -B -u launcher.py
goto finished

:pylauncher
py -3 -B -u launcher.py
goto finished

:python
python -B -u launcher.py

:finished
set "launch_result=%errorlevel%"
echo.
echo Frontend has exited. Closing this window does not stop the robot.
pause
exit /b %launch_result%
