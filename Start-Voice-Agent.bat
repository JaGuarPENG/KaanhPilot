@echo off
chcp 65001 >nul
setlocal
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
echo [Voice Agent] Preparing startup...
cd /d "%~dp0voice-agent"
if errorlevel 1 (
    echo [ERROR] Cannot open the application directory.
    goto end
)
if exist "%~dp0.venv\Scripts\python.exe" (
    set "KAANH_PYTHON=%~dp0.venv\Scripts\python.exe"
    echo [Voice Agent] Using project .venv.
    goto launch
)
rem Only fall back to Conda pyagent, never to an unrelated system Python.
if defined KAANH_CONDA_BAT goto activate_conda
if defined CONDA_EXE for %%I in ("%CONDA_EXE%") do set "KAANH_CONDA_BAT=%%~dpI..\condabin\conda.bat"
if exist "%KAANH_CONDA_BAT%" goto activate_conda
rem Local installation; set KAANH_CONDA_BAT on PCs with a different location.
set "KAANH_CONDA_BAT=D:\anaconda3\condabin\conda.bat"
:activate_conda
if not exist "%KAANH_CONDA_BAT%" (
    echo [ERROR] Project .venv is absent and Conda was not found.
    echo Set KAANH_CONDA_BAT to your Anaconda condabin\conda.bat path.
    goto end
)
echo [Voice Agent] Activating Conda environment: pyagent
call "%KAANH_CONDA_BAT%" activate pyagent
if errorlevel 1 (
    echo [ERROR] Could not activate Conda environment pyagent.
    goto end
)
set "KAANH_PYTHON=%CONDA_PREFIX%\python.exe"
:launch
echo [Voice Agent] Working directory: %CD%
"%KAANH_PYTHON%" -c "import sys; print('Python:', sys.executable); print('Version:', sys.version); sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 (
    echo [ERROR] Selected Python could not start or is older than 3.10.
    goto end
)
if /i "%~1"=="--check-env" goto end
echo [Voice Agent] Starting application...
"%KAANH_PYTHON%" -B -u -m voice_agent.runtime.cli %*
set "KAANH_EXIT_CODE=%ERRORLEVEL%"
echo [Voice Agent] Application exited. Exit code: %KAANH_EXIT_CODE%
if not "%KAANH_EXIT_CODE%"=="0" echo [ERROR] Check messages above. Install root requirements.txt into the selected environment.
:end
pause
endlocal
