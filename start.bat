@echo off
REM ====================================================================
REM  Trading Desk -- server start karein. Roz yahi file chalani hai.
REM ====================================================================
cd /d "%~dp0"
title Trading Desk

if not exist ".venv\Scripts\python.exe" (
    echo  Setup nahi hua. Pehle setup.bat chalayein.
    pause
    exit /b 1
)
if not exist ".env" (
    echo  .env file nahi mili. Pehle setup.bat chalayein.
    pause
    exit /b 1
)
if not exist "frontend\dist\index.html" (
    echo  Frontend build nahi mila. Pehle setup.bat chalayein.
    pause
    exit /b 1
)

REM Galat CA-bundle paths hata dein (PostgreSQL jaise software set kar dete hain).
set "SSL_CERT_FILE="
set "REQUESTS_CA_BUNDLE="
set "CURL_CA_BUNDLE="

echo.
echo   Trading Desk chal raha hai:  http://127.0.0.1:8000
echo   Band karne ke liye is window me Ctrl+C dabayein.
echo.

start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

pause
