@echo off
title ISHTI - Your Personal AI Assistant
color 0B
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ========================================================================
echo   ISHTI INTELLIGENT PERSONAL ASSISTANT
echo   Zero-Cost Local Architecture (RTX 5070 Ti)
echo ========================================================================
echo.

:: 1. Verify and Activate Python Virtual Environment
if not exist "%~dp0venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found at %~dp0venv
    echo Creating virtual environment...
    python -m venv "%~dp0venv"
    if errorlevel 1 (
        echo [FATAL] Failed to create virtual environment. Ensure Python 3.11 is installed.
        pause
        exit /b 1
    )
)

call "%~dp0venv\Scripts\activate.bat"

:: 2. Singleton-instance check: if ISHTI is already running, focus existing window and exit
if "%1"=="" (
    "%~dp0venv\Scripts\python.exe" "%~dp0desktop\focus_ishti.py"
    if !errorlevel! equ 0 (
        echo [INFO] ISHTI is already running. Existing instance brought to focus.
        exit /b 0
    )
)

:: 3. CLI commands support
if "%1"=="doctor" (
    python "orchestrator\doctor.py"
    exit /b %errorlevel%
)

if "%1"=="search" (
    python -c "import sys; from tools.web.search import web_search; res = web_search(sys.argv[1], 5); [print(f'[{i+1}] {r.title} ({r.source})\n    {r.url}\n    {r.snippet}\n') for i, r in enumerate(res)]" %2
    exit /b %errorlevel%
)

if "%1"=="rebenchmark" (
    python "orchestrator\model_router.py"
    exit /b %errorlevel%
)

if "%1"=="find" (
    python -c "import sys; from desktop.computer_use import get_computer_use_agent; res = get_computer_use_agent().search_pc(sys.argv[1]); [print(f'[{i+1}] {r[\"name\"]} ({r[\"size\"]})\n    {r[\"path\"]}') for i, r in enumerate(res)]" %2
    exit /b %errorlevel%
)

if "%1"=="server" (
    set PORT=8888
    if not "%2"=="" set PORT=%2
    echo [ISHTI] Starting web server on port !PORT!...
    python -m ishti !PORT!
    exit /b %errorlevel%
)

if "%1"=="cli" (
    "%~dp0venv\Scripts\python.exe" "%~dp0orchestrator\startup.py" --cli
    exit /b %errorlevel%
)

:: 3. Launch Default Startup Sequence & Health-Checks
"%~dp0venv\Scripts\python.exe" "%~dp0orchestrator\startup.py" %*

if errorlevel 1 (
    echo.
    echo [ISHTI EXITED WITH ERROR] Press any key to close.
    pause >nul
    exit /b 1
)

:: Auto-close CMD console window so desktop application runs seamlessly
exit
