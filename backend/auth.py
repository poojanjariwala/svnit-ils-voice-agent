# backend/auth.py
# ─────────────────────────────────────────────
# Task   : Self-serve accounts — signup/login, password hashing,
#          HMAC-signed bearer tokens, and the FastAPI dependency
#          that protects business endpoints.
#
# Design notes:
#   - PBKDF2-HMAC-SHA256 (stdlib hashlib) with per-user salt.
#     No new dependencies; strong for this scale.
#   - Token = "uid.expiry.signature" where signature = HMAC-SHA256
#     over "uid.expiry" with SECRET_KEY. Stateless, no session table.
#   - Voice webhooks stay unauthenticated: Vonage calls them from
#     their network and cannot present our tokens.
# ─────────────────────────────────────────────
import os
import hashlib
import hmac
import secrets
import time
import logging

logger = logging.getLogger(__name__)

# Token lifetime: 7 days
TOKEN_TTL_SECONDS = 7 * 24 * 3600
PBKDF2_ITERATIONS = 120_000

_dev_key_warning_shown = False


def _secret_key() -> bytes:
    global _dev_key_warning_shown
    key = os.getenv("SECRET_KEY", "")
    if not key:
        if not _dev_key_warning_shown:
            logger.warning("SECRET_KEY not set — using a dev fallback key. Set SECRET_KEY in .env for production.")
            _dev_key_warning_shown = True
        return b"dev-insecure-secret-key-change-me"
    return key.encode("utf-8")


def hash_password(password: str) -> str:
    """PBKDF2 hash: format 'salt_hex$hash_hex'"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_token(user_id: str) -> str:
    """Stateless signed token: uid.expiry.signature"""
    expiry = str(int(time.time()) + TOKEN_TTL_SECONDS)
    payload = f"{user_id}.{expiry}"
    sig = hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str):
    """Return user_id if valid & unexpired, else None"""
    try:
        user_id, expiry, sig = token.split(".", 2)
        payload = f"{user_id}.{expiry}"
        expected = hmac.new(_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expiry) < time.time():
            return None
        return user_id
    except (ValueError, TypeError):
        return None


# ── FastAPI dependency ───────────────────────────────────────
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolve the bearer token to a User; 401 if missing/invalid/unknown."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token. Log in first.")
    user_id = verify_token(auth_header[7:].strip())
    if not user_id:
        raise HTTPException(401, "Invalid or expired token. Log in again.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(401, "Account no longer exists.")
    return user
