@echo off
REM ============================================================================
REM 508 Agent — first-time setup
REM ----------------------------------------------------------------------------
REM Installs Python and npm dependencies.  Run once before the first launch.
REM Subsequent launches: use start.bat.
REM ============================================================================

setlocal
set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend\frontend"

echo === 508 Agent — first-time setup ===
echo.

REM ---------------------------------------------------------------------------
REM Backend deps
REM ---------------------------------------------------------------------------
echo [1/2] Installing Python dependencies...
echo       (running: pip install -r requirements.txt)
pushd "%BACKEND%" || (
    echo [setup.bat] ERROR: cannot enter "%BACKEND%".
    pause
    exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [setup.bat] Backend install FAILED. Read the error above.
    popd
    pause
    exit /b 1
)
popd
echo.

REM ---------------------------------------------------------------------------
REM Frontend deps
REM ---------------------------------------------------------------------------
echo [2/2] Installing Node dependencies...
echo       (running: npm install)
pushd "%FRONTEND%" || (
    echo [setup.bat] ERROR: cannot enter "%FRONTEND%".
    pause
    exit /b 1
)
call npm install
if errorlevel 1 (
    echo [setup.bat] Frontend install FAILED. Read the error above.
    popd
    pause
    exit /b 1
)
popd
echo.

echo ============================================================================
echo Setup complete.
echo.
echo Next:  double-click start.bat to launch the app.
echo ============================================================================
endlocal
pause
