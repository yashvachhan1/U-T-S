"""Thin async wrapper around the Upstox REST API (v2/v3)."""
from __future__ import annotations

import json
import ssl
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import certifi
import httpx

from ..config import (
    API_KEY,
    API_SECRET,
    REDIRECT_URI,
    TOKEN_PATH,
    UPSTOX_BASE,
    credentials_ok,
)


# Some installers (PostgreSQL, for one) leave CURL_CA_BUNDLE / SSL_CERT_FILE
# pointing at a path that does not exist, which breaks every HTTPS call with a
# confusing error.  Pin the CA bundle to certifi so we never depend on that.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class UpstoxError(Exception):
    def __init__(self, message: str, status: int = 400, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload


# --------------------------------------------------------------------------
# Token storage.  Upstox access tokens die at ~03:30 IST every day, so we just
# persist it to disk and ask the user to log in again when it expires.
# --------------------------------------------------------------------------
_token: dict[str, Any] | None = None


def _load_token() -> dict[str, Any] | None:
    global _token
    if _token is None and TOKEN_PATH.exists():
        try:
            _token = json.loads(TOKEN_PATH.read_text())
        except Exception:
            _token = None
    return _token


def save_token(data: dict[str, Any]) -> None:
    global _token
    data = dict(data)
    data["saved_at"] = datetime.now().isoformat(timespec="seconds")
    TOKEN_PATH.write_text(json.dumps(data, indent=2))
    _token = data


def clear_token() -> None:
    global _token
    _token = None
    TOKEN_PATH.unlink(missing_ok=True)


def access_token() -> str | None:
    t = _load_token()
    return t.get("access_token") if t else None


def token_expiry() -> datetime | None:
    """Upstox tokens expire at 03:30 the morning after they were issued."""
    t = _load_token()
    if not t or not t.get("saved_at"):
        return None
    saved = datetime.fromisoformat(t["saved_at"])
    expiry = saved.replace(hour=3, minute=30, second=0, microsecond=0)
    if saved >= expiry:
        expiry += timedelta(days=1)
    return expiry


def session_info() -> dict[str, Any]:
    t = _load_token()
    exp = token_expiry()
    return {
        "logged_in": bool(t and t.get("access_token") and (exp is None or exp > datetime.now())),
        "credentials_configured": credentials_ok(),
        "user_name": (t or {}).get("user_name"),
        "user_id": (t or {}).get("user_id"),
        "email": (t or {}).get("email"),
        "expires_at": exp.isoformat(timespec="seconds") if exp else None,
    }


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------
def login_url(state: str = "upstox") -> str:
    q = urlencode(
        {
            "client_id": API_KEY,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{UPSTOX_BASE}/v2/login/authorization/dialog?{q}"


async def exchange_code(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30, verify=SSL_CONTEXT) as c:
        r = await c.post(
            f"{UPSTOX_BASE}/v2/login/authorization/token",
            headers={
                "accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "code": code,
                "client_id": API_KEY,
                "client_secret": API_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
    body = _parse(r)
    if "access_token" not in body:
        raise UpstoxError(_err_msg(body, "Token exchange failed"), r.status_code, body)
    save_token(body)
    return body


# --------------------------------------------------------------------------
# Generic request helper
# --------------------------------------------------------------------------
def _parse(r: httpx.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text, "_status": r.status_code}


def _err_msg(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        errs = body.get("errors")
        if isinstance(errs, list) and errs:
            e = errs[0]
            return f"{e.get('message') or fallback} ({e.get('errorCode', '')})".strip()
        if body.get("message"):
            return str(body["message"])
        if body.get("error_description"):
            return str(body["error_description"])
    return fallback


async def request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    auth: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    headers = {"accept": "application/json"}
    if auth:
        tok = access_token()
        if not tok:
            raise UpstoxError("Upstox me login nahi hai. Pehle Login dabayein.", 401)
        headers["Authorization"] = f"Bearer {tok}"
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)

    url = path if path.startswith("http") else f"{UPSTOX_BASE}{path}"
    async with httpx.AsyncClient(timeout=30, verify=SSL_CONTEXT) as c:
        r = await c.request(method, url, headers=headers, params=params, json=json_body)

    body = _parse(r)
    if r.status_code == 401:
        raise UpstoxError("Upstox session expire ho gaya. Dobara login karein.", 401, body)
    if r.status_code >= 400 or (isinstance(body, dict) and body.get("status") == "error"):
        raise UpstoxError(_err_msg(body, f"Upstox API error {r.status_code}"), r.status_code, body)
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


async def get(path: str, **kw) -> Any:
    return await request("GET", path, **kw)


async def post(path: str, **kw) -> Any:
    return await request("POST", path, **kw)


async def delete(path: str, **kw) -> Any:
    return await request("DELETE", path, **kw)


async def put(path: str, **kw) -> Any:
    return await request("PUT", path, **kw)
