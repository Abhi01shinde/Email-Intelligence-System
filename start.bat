@echo off
echo ========================================
echo   Email Intelligence System - Startup
echo ========================================
echo.

:: Create virtual environment if not exists
if not exist "venv" (
    echo [1/4] Creating virtual environment...
    python -m venv venv
)

:: Activate venv
echo [2/4] Activating virtual environment...
call venv\Scripts\activate.bat

:: Install dependencies
echo [3/4] Installing dependencies...
pip install -r requirements.txt --quiet

:: Create data directory
if not exist "data" mkdir data

:: Start server
echo [4/4] Starting server...
echo.
echo ✅ Dashboard: http://localhost:8000
echo ✅ API Docs:   http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop the server
echo.
python main.py
pause
