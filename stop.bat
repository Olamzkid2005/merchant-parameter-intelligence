@echo off
REM stop.bat — Stop the running Merchant Intelligence services (API + Web).
REM Uses `python app.start stop`, which kills processes tracked by app.start
REM (PID files in logs/ plus anything listening on ports 8000 / 5173).
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Stopping Merchant Intelligence services...
echo ============================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app.start stop
) else (
    python app.start stop
)

if errorlevel 1 (
    echo.
    echo [X] Something went wrong - see the message above.
) else (
    echo.
    echo [OK] Services stopped.
)
echo.
pause
