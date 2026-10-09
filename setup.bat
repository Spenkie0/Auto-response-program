@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ================================================================
echo SCRAPPER OLLAMA - COMPLETE WINDOWS SETUP
echo ================================================================
echo This is the single installation entry point.
echo.

REM ================================================================
REM WinGet / App Installer prerequisite
REM ================================================================
where winget >nul 2>&1
if errorlevel 1 (
    echo [ERROR] WinGet is not available.
    echo Install Microsoft App Installer / WinGet, then rerun setup.bat.
    pause
    exit /b 1
)

REM ================================================================
REM Python 3.13
REM ================================================================
set "PY_CMD="
where py >nul 2>&1
if not errorlevel 1 (
    py -3.13 -c "import sys; print(sys.version)" >nul 2>&1
    if not errorlevel 1 set "PY_CMD=py -3.13"
)

if not defined PY_CMD (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,13) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY_CMD=python"
    )
)

if not defined PY_CMD (
    echo [DEPENDENCY] Installing Python 3.13...
    winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements --silent
    if errorlevel 1 (
        echo [ERROR] Python 3.13 installation failed.
        pause
        exit /b 1
    )
    echo [SETUP] Relaunching setup with the newly installed Python...
    start "" /wait "%ComSpec%" /d /c call "%~f0"
    exit /b %ERRORLEVEL%
)

echo [DEPENDENCY] Python: OK

REM ================================================================
REM Host Python requirements - intentionally embedded here so there is
REM no separate root requirements.txt installation step.
REM ================================================================
%PY_CMD% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [DEPENDENCY] Bootstrapping pip...
    %PY_CMD% -m ensurepip --upgrade
    if errorlevel 1 (
        echo [ERROR] Could not bootstrap pip.
        pause
        exit /b 1
    )
)

echo [DEPENDENCY] Installing host Python requirements...
%PY_CMD% -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] pip upgrade failed.
    pause
    exit /b 1
)

%PY_CMD% -m pip install "beautifulsoup4" "defusedxml>=0.7.1"
if errorlevel 1 (
    echo [ERROR] Host Python dependency installation failed.
    pause
    exit /b 1
)

REM ================================================================
REM External application dependencies
REM ================================================================
REM IMPORTANT: do not call winget install directly here.
REM scripts\setup_windows.py is the single dependency authority and
REM verifies an application before deciding whether installation is needed.
REM This prevents setup.bat from reinstalling/upgrading an already installed
REM Ollama (or any other configured application).
echo [DEPENDENCY] External applications will be verified by setup_windows.py.

REM ================================================================
REM Final verification/configuration:
REM - Tesseract language data
REM - Docker daemon
REM - Docker quarantine image
REM - configured Ollama models
REM - all configured dependency checks
REM ================================================================
echo.
echo [SETUP] Running final dependency verification and configuration...
%PY_CMD% scripts\setup_windows.py --yes
if errorlevel 1 (
    echo.
    echo [ERROR] Final dependency verification failed.
    pause
    exit /b 1
)

REM ================================================================
REM Native Firefox Messaging registration
REM ================================================================
echo [SETUP] Registering Firefox Native Messaging host...
%PY_CMD% native_messaging\install_native_host.py
if errorlevel 1 (
    echo [ERROR] Native Messaging registration failed.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo SETUP COMPLETE
echo ================================================================
echo Python host packages   : beautifulsoup4, defusedxml
echo Firefox                : installed/verified
echo Docker Desktop         : installed/verified
echo Tesseract OCR         : installed/verified
echo Ollama                 : installed/verified
echo Ollama models          : checked/pulled by final verification
echo Quarantine image       : checked/built by final verification
echo Native Messaging       : registered
echo.
echo Documentation          : information\INFORMATION.md
echo.
pause
exit /b 0
