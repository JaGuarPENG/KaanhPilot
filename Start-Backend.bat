@echo off
chcp 65001 >nul
setlocal
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
echo [Backend] Preparing startup...
cd /d "%~dp0"
if errorlevel 1 (
    echo [ERROR] Cannot open the application directory.
    goto end
)
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
    if not errorlevel 1 (
        set "KAANH_PYTHON=%~dp0.venv\Scripts\python.exe"
        echo [Backend] Using project .venv.
        goto launch
    )
)
set "KAANH_CONFIG_FILE=%~dp0config\launcher\launcher_config.json"
set "KAANH_CONFIGURED_PYTHON="
for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -Command "$ErrorActionPreference = 'Stop'; $config = Get-Content -LiteralPath $env:KAANH_CONFIG_FILE -Raw -Encoding UTF8 | ConvertFrom-Json; $p = ([string]$config.python_executable).Trim(); if ($p) { if ($p.StartsWith('~/') -or $p.StartsWith('~\')) { $p = Join-Path $env:USERPROFILE $p.Substring(2) }; if (-not [IO.Path]::IsPathRooted($p)) { $p = Join-Path (Split-Path $env:KAANH_CONFIG_FILE) $p }; Write-Output $p }"`) do set "KAANH_CONFIGURED_PYTHON=%%P"
if not defined KAANH_CONFIGURED_PYTHON goto find_conda
if not exist "%KAANH_CONFIGURED_PYTHON%" goto find_conda
"%KAANH_CONFIGURED_PYTHON%" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 goto find_conda
set "KAANH_PYTHON=%KAANH_CONFIGURED_PYTHON%"
echo [Backend] Using launcher_config.python_executable.
goto launch
:find_conda
rem Only fall back to Conda pyagent, never to an unrelated system Python.
if defined KAANH_CONDA_BAT goto activate_conda
if defined CONDA_EXE for %%I in ("%CONDA_EXE%") do set "KAANH_CONDA_BAT=%%~dpI..\condabin\conda.bat"
if exist "%KAANH_CONDA_BAT%" goto activate_conda
rem Local installation; set KAANH_CONDA_BAT on PCs with a different location.
set "KAANH_CONDA_BAT=D:\anaconda3\condabin\conda.bat"
:activate_conda
if not exist "%KAANH_CONDA_BAT%" (
    echo [ERROR] No usable .venv or configured Python, and Conda was not found.
    echo Set KAANH_CONDA_BAT to your Anaconda condabin\conda.bat path.
    goto end
)
echo [Backend] Activating Conda environment: pyagent
call "%KAANH_CONDA_BAT%" activate pyagent
if errorlevel 1 (
    echo [ERROR] Could not activate Conda environment pyagent.
    goto end
)
set "KAANH_PYTHON=%CONDA_PREFIX%\python.exe"
:launch
echo [Backend] Working directory: %CD%
"%KAANH_PYTHON%" -c "import sys; print('Python:', sys.executable); print('Version:', sys.version); sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 (
    echo [ERROR] Selected Python could not start or is older than 3.10.
    goto end
)
if /i "%~1"=="--check-env" goto end
echo [Backend] Starting application...
rem Prevent launcher.py from overriding the environment selected above.
set "KAANH_BACKEND_PYTHON_SELECTED=1"
"%KAANH_PYTHON%" -B -u "%~dp0launcher.py" %*
set "KAANH_EXIT_CODE=%ERRORLEVEL%"
echo [Backend] Application exited. Exit code: %KAANH_EXIT_CODE%
if not "%KAANH_EXIT_CODE%"=="0" echo [ERROR] Check messages above. Install root requirements.txt into the selected environment.
:end
pause
endlocal
