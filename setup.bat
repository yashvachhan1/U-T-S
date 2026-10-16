@echo off
REM ====================================================================
REM  Trading Desk -- pehli baar setup. Sirf ek baar chalana hai.
REM  Backend: Python + FastAPI   |   Frontend: React + Vite
REM ====================================================================
cd /d "%~dp0"
title Trading Desk - Setup

REM Kuch software (jaise PostgreSQL) ye variables galat path par set kar dete
REM hain, jisse har HTTPS call fail ho jati hai. Yahan unhe hata rahe hain.
set "SSL_CERT_FILE="
set "REQUESTS_CA_BUNDLE="
set "CURL_CA_BUNDLE="

echo.
echo  [1/4] Python virtual environment...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo  ERROR: Python nahi mila. python.org se Python 3.11+ install karein,
        echo         aur install karte waqt "Add Python to PATH" tick karein.
        pause
        exit /b 1
    )
)

echo  [2/4] Python libraries install ho rahi hain...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo  ERROR: Python libraries install nahi hui. Internet check karein.
    pause
    exit /b 1
)

echo  [3/4] React frontend build ho raha hai (pehli baar 1-2 min)...
where npm >nul 2>nul
if errorlevel 1 (
    echo.
    echo  ERROR: Node.js / npm nahi mila. nodejs.org se Node 18+ install karein.
    pause
    exit /b 1
)
pushd frontend
call npm install --no-audit --no-fund
if errorlevel 1 ( echo  ERROR: npm install fail. & popd & pause & exit /b 1 )
call npm run build
if errorlevel 1 ( echo  ERROR: frontend build fail. & popd & pause & exit /b 1 )
popd

echo  [4/4] Config file...
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo  ================================================================
    echo   .env file ban gayi hai.
    echo   Ab usme apni Upstox API KEY aur SECRET bharein.
    echo   Kaise milegi -- README.md ka section 2 padhein.
    echo  ================================================================
    notepad ".env"
) else (
    echo  .env pehle se maujood hai -- chhod diya.
)

echo.
echo  Setup poora hua. Ab start.bat chalayein.
echo.
pause
