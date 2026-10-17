@echo off
title NFSU Library Self-Service Kiosk
echo ============================================================
echo   NFSU Central Library Self-Service Kiosk
echo   Koha 26 Integration (117.239.182.15:6001)
echo ============================================================
echo.

if exist "..\.venv\Scripts\activate.bat" (
    echo Activating virtual environment (..\.venv)...
    call ..\.venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    echo Activating virtual environment (.venv)...
    call .venv\Scripts\activate.bat
) else (
    echo Virtual environment not found. Creating .venv...
    python -m venv .venv
    call .venv\Scripts\activate.bat
)

python -m pip install -r requirements.txt
echo.
echo Starting Kiosk Server at http://localhost:8050 ...
start http://localhost:8050
python -m uvicorn main:app --host 127.0.0.1 --port 8050 --reload
pause
