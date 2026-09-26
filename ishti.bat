@echo off
setlocal
cd /d "%~dp0"

if "%1"=="doctor" (
    call "venv\Scripts\activate.bat"
    python "orchestrator\doctor.py"
    exit /b %errorlevel%
)

if "%1"=="search" (
    call "venv\Scripts\activate.bat"
    python -c "from tools.web.search import web_search; res = web_search('%~2', 5); [print(f'[{i+1}] {r.title} ({r.source})\n    {r.url}\n    {r.snippet}\n') for i, r in enumerate(res)]"
    exit /b %errorlevel%
)

if "%1"=="rebenchmark" (
    call "venv\Scripts\activate.bat"
    python "orchestrator\model_router.py"
    exit /b %errorlevel%
)

if "%1"=="find" (
    call "venv\Scripts\activate.bat"
    python -c "from desktop.computer_use import get_computer_use_agent; res = get_computer_use_agent().search_pc('%~2'); [print(f'[{i+1}] {r[\"name\"]} ({r[\"size\"]})\n    {r[\"path\"]}') for i, r in enumerate(res)]"
    exit /b %errorlevel%
)

:: Default: launch startup
call "start.bat"
