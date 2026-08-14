@echo off
rem Launch the legacy Streamlit Merchant Intelligence web UI (uses project venv).
rem start_app.py lives in archive/ but runs against the project root.
cd /d "%~dp0\.."
start "" ".venv\Scripts\python.exe" "archive\start_app.py" --launch
