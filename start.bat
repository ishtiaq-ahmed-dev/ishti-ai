@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: 1. If CLI developer commands are passed (%1 is not empty), handle in console
if not "%1"=="" goto handle_cli

:: 2. Launch isolated desktop application directly with animated startup screen (NO CMD WINDOW)
if exist "%~dp0dist\ISHTI\ISHTI.exe" (
    start "" "%~dp0dist\ISHTI\ISHTI.exe"
    exit /b 0
)

if exist "%~dp0venv\Scripts\pythonw.exe" (
    start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0desktop\launcher.py"
    exit /b 0
)

:: No venv yet: ISHTI has not been installed on this PC
call :need_install
exit /b 1

:handle_cli
if not exist "%~dp0venv\Scripts\python.exe" (
    call :need_install
    exit /b 1
)
call "%~dp0venv\Scripts\activate.bat"

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

if "%1"=="settings" (
    echo [ISHTI] Opening the Settings screen...
    start "" http://127.0.0.1:8888/settings
    exit /b 0
)

if "%1"=="test" (
    echo [ISHTI] Running the offline test suite...
    python -m pytest
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

:: Default fallback with args
"%~dp0venv\Scripts\python.exe" "%~dp0orchestrator\startup.py" %*
exit /b %errorlevel%

:need_install
echo.
echo  ISHTI is not installed on this PC yet.
echo  Please double-click install.bat in this folder first. It sets everything up for you.
echo.
pause
exit /b 1
