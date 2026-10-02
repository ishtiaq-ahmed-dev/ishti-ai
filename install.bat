@echo off
setlocal
cd /d "%~dp0"
title ISHTI setup
echo.
echo  ISHTI setup
echo  ===========
echo.

:: 1. Find Python 3.11 (the version ISHTI is built for)
set "PY="
py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY call :find_known_python
if defined PY goto run

:: 2. Not found: explain, and offer to install it
echo  ISHTI needs a free program called Python 3.11, and it is not on this PC yet.
echo  I can install it for you now (small download, no questions asked afterwards).
echo.
choice /c YN /m "  Install Python 3.11 now"
if errorlevel 2 goto no_python
winget --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  This PC cannot install it automatically ^(the "winget" tool is missing^).
    echo  Please open https://www.python.org/downloads/release/python-3119/ , download the
    echo  "Windows installer ^(64-bit^)", tick "Add python.exe to PATH", install it,
    echo  then double-click install.bat again.
    goto end
)
winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
call :find_known_python
if defined PY goto run
echo.
echo  Python was installed, but this window cannot see it yet.
echo  Please close this window and double-click install.bat once more.
goto end

:no_python
echo.
echo  OK. Install Python 3.11 yourself from https://www.python.org/downloads/release/python-3119/
echo  (tick "Add python.exe to PATH"), then double-click install.bat again.
goto end

:run
%PY% "%~dp0tools\install.py" %*
if errorlevel 1 (
    echo.
    echo  Setup did not finish. Read the messages above, fix what they say, and double-click install.bat again.
    echo  It is safe to run it as many times as you like: it continues where it stopped.
) else (
    echo.
)
goto end

:find_known_python
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "%ProgramFiles%\Python311\python.exe") do (
    if exist %%P set "PY=%%P"
)
exit /b 0

:end
echo.
pause
exit /b 0
