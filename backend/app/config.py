"""Configuration loaded from .env (project root)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]   # .../trading-app
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = ROOT / "frontend"

API_KEY = os.getenv("UPSTOX_API_KEY", "").strip()
API_SECRET = os.getenv("UPSTOX_API_SECRET", "").strip()
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/callback").strip()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() in ("1", "true", "yes")

DB_PATH = DATA_DIR / "trading.db"
TOKEN_PATH = DATA_DIR / "token.json"
INSTRUMENTS_PATH = DATA_DIR / "instruments.json"

UPSTOX_BASE = "https://api.upstox.com"


def credentials_ok() -> bool:
    return bool(API_KEY and API_SECRET and not API_KEY.startswith("your_"))
