@echo off
title ISHTI - Stop Assistant
color 0C
setlocal
cd /d "%~dp0"

echo ========================================================================
echo   STOPPING ISHTI ASSISTANT
echo ========================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$stopped = 0;" ^
    "$ports = @(8888, 8899);" ^
    "foreach ($p in $ports) {" ^
    "    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue;" ^
    "    if ($conns) {" ^
    "        foreach ($c in $conns) {" ^
    "            $targetPid = $c.OwningProcess;" ^
    "            if ($targetPid -and $targetPid -gt 4) {" ^
    "                try {" ^
    "                    $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue;" ^
    "                    if ($proc -and ($proc.ProcessName -like 'python*' -or $proc.ProcessName -like 'node*' -or $proc.ProcessName -like 'uvicorn*')) {" ^
    "                        Write-Host ('  [x] Stopping listener on port ' + $p + ' (PID: ' + $targetPid + ', ' + $proc.ProcessName + ')...') -ForegroundColor Yellow;" ^
    "                        Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue;" ^
    "                        $stopped++;" ^
    "                    }" ^
    "                } catch {}" ^
    "            }" ^
    "        }" ^
    "    }" ^
    "};" ^
    "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%'\" | Where-Object {" ^
    "    $cmd = $_.CommandLine;" ^
    "    if (-not $cmd) { return $false };" ^
    "    if ($cmd -match 'ms-python' -or $cmd -match 'antigravity-ide' -or $cmd -match 'vscode') { return $false };" ^
    "    return ($cmd -match 'ishti' -or $cmd -match 'startup\.py' -or $cmd -match 'server\.py')" ^
    "} | ForEach-Object {" ^
    "    $targetPid = $_.ProcessId;" ^
    "    if ($targetPid -and $targetPid -gt 4) {" ^
    "        try {" ^
    "            Write-Host ('  [x] Stopping ISHTI background process (PID: ' + $targetPid + ')...') -ForegroundColor Yellow;" ^
    "            Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue;" ^
    "            $stopped++;" ^
    "        } catch {}" ^
    "    }" ^
    "};" ^
    "if ($stopped -gt 0) {" ^
    "    Write-Host '`n[SUCCESS] All ISHTI services have been stopped.' -ForegroundColor Green;" ^
    "} else {" ^
    "    Write-Host '[INFO] No running ISHTI services found.' -ForegroundColor Cyan;" ^
    "}"

echo.
timeout /t 2 >nul 2>&1
exit /b 0
