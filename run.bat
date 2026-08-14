@echo off
rem ============================================================
rem  Merchant Intelligence - one-click launcher (React app)
rem
rem  Double-click this file to:
rem    1. start the backend  (http://127.0.0.1:8000)
rem    2. start the frontend (http://localhost:5173)
rem    3. open your browser to the app
rem
rem  Press Ctrl+C in this window to stop both services.
rem  If the app is already running it will be reused, not restarted.
rem ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [X] venv not found at .venv\Scripts\python.exe
    echo     Create it first:  python -m venv .venv
    echo     then install deps: .venv\Scripts\python.exe -m pip install fastapi uvicorn pandas openpyxl
    pause
    exit /b 1
)

echo.
echo  Starting Merchant Intelligence...
echo    Frontend : http://localhost:5173
echo    API      : http://127.0.0.1:8000
echo    Press Ctrl+C to stop both services.
echo.
".venv\Scripts\python.exe" app.start app --open

if errorlevel 1 (
    echo.
    echo  [X] Startup FAILED - see the messages above.
    echo      Tip: if it says a port is occupied by another program,
    echo      close that program first, then try again.
) else (
    echo.
    echo  [OK] Services have stopped cleanly.
)
pause
