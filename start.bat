@echo off
REM ============================================================================
REM 508 Agent — one-click launcher
REM ----------------------------------------------------------------------------
REM Opens two terminal windows:
REM   1. Backend (Python / uvicorn via dev_run.py)
REM   2. Frontend (Expo web)
REM
REM Then opens http://localhost:8081 in your default browser after a short
REM delay so the Expo dev server has time to start.
REM
REM Prerequisites (one-time setup):
REM   * Python 3.10+ on PATH:    python --version
REM   * Node 18+ on PATH:        node --version
REM   * Backend deps installed:  cd backend ^&^& pip install -r requirements.txt
REM   * Frontend deps installed: cd frontend\frontend ^&^& npm install
REM ============================================================================

setlocal
set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend\frontend"

if not exist "%BACKEND%\dev_run.py" (
    echo [start.bat] ERROR: backend\dev_run.py not found at "%BACKEND%".
    pause
    exit /b 1
)
if not exist "%FRONTEND%\package.json" (
    echo [start.bat] ERROR: frontend\frontend\package.json not found at "%FRONTEND%".
    pause
    exit /b 1
)

echo [start.bat] Launching backend...
start "508 Agent — backend" cmd /k "cd /d ""%BACKEND%"" && python dev_run.py"

echo [start.bat] Launching frontend (this window may take ~30s on first run)...
start "508 Agent — frontend" cmd /k "cd /d ""%FRONTEND%"" && npm run web"

echo [start.bat] Waiting 8 seconds for the dev servers to come up...
timeout /t 8 /nobreak >nul

echo [start.bat] Opening http://localhost:8081 in your browser...
start "" "http://localhost:8081"

echo.
echo Both servers are running in their own windows. Close those windows to stop.
echo If the browser tab loaded before the frontend was ready, just refresh it.
echo.
endlocal
