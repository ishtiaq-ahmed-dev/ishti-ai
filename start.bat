@echo off
title ISHTI - Your Personal AI Assistant
color 0B
cd /d "D:\ISHTI"

echo ========================================================================
echo   ISHTI INTELLIGENT PERSONAL ASSISTANT
echo   Zero-Cost Local Architecture (RTX 5070 Ti)
echo ========================================================================
echo.

:: 1. Verify and Activate Python Virtual Environment
if not exist "D:\ISHTI\venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found at D:\ISHTI\venv
    echo Creating virtual environment...
    python -m venv "D:\ISHTI\venv"
    if errorlevel 1 (
        echo [FATAL] Failed to create virtual environment. Ensure Python 3.11 is installed.
        pause
        exit /b 1
    )
)

call "D:\ISHTI\venv\Scripts\activate.bat"

:: 2. Launch Startup Sequence & Health-Checks
"D:\ISHTI\venv\Scripts\python.exe" "D:\ISHTI\orchestrator\startup.py"

if errorlevel 1 (
    echo.
    echo [ISHTI EXITED WITH ERROR] Press any key to close.
    pause >nul
)
