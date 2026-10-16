@echo off
title NFSU Library Self-Service Kiosk
echo ============================================================
echo   NFSU Central Library Self-Service Kiosk
echo   Koha 26 Integration (117.239.182.15:6001)
echo ============================================================
echo.

python -m pip install -r requirements.txt
echo.
echo Starting Kiosk Server at http://localhost:8000 ...
start http://localhost:8000
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
