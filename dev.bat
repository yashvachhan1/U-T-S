@echo off
REM ====================================================================
REM  Sirf development ke liye: React hot-reload ke saath.
REM  Do windows khulti hain -- API (8000) aur Vite dev server (5173).
REM  Browser me http://localhost:5173 kholein.
REM ====================================================================
cd /d "%~dp0"

set "SSL_CERT_FILE="
set "REQUESTS_CA_BUNDLE="
set "CURL_CA_BUNDLE="

start "Trading Desk API" ".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
cd frontend
npm run dev
