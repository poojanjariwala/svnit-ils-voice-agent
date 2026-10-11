# backend/twilio_security.py
# ─────────────────────────────────────────────
# Task   : Verify that a /twilio/* request really came from Twilio.
#
# Why: every webhook here is public by necessity — Twilio has to reach them
# over the internet. Without a signature check, anyone who learns the URL can
# POST a fake call, a fake speech result or a fake lead, and it lands in the
# CRM as if it were real.
#
# How: Twilio signs each request with HMAC-SHA1 over the exact URL it called
# plus every POST parameter, keyed on the account auth token, and sends the
# result in X-Twilio-Signature. We rebuild the same string and compare.
# ─────────────────────────────────────────────
import base64
import hashlib
import hmac
import logging
import os
from urllib.parse import urlparse

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

SIG_HEADER = "X-Twilio-Signature"


def public_url_for(request: Request) -> str:
    """
    Rebuild the URL Twilio actually called.

    Behind a proxy (Render, Railway, nginx) the app sees its internal
    address, so the scheme and host come from X-Forwarded-* when present.
    Getting this wrong yields a valid signature mismatch, not a security hole.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")

    if forwarded_proto and forwarded_host:
        url = f"{forwarded_proto}://{forwarded_host}{request.url.path}"
    else:
        url = str(request.url)

    if request.url.query:
        url = f"{url}?{request.url.query}"
    return url


def expected_signature(url: str, params: dict, auth_token: str) -> str:
    """
    Twilio's algorithm: HMAC-SHA1 over url + sorted "key<value>" pairs,
    base64-encoded. Multi-valued params are serialised key<value>,key<value>.

    Verified byte-for-byte against twilio-python's RequestValidator.compute_signature.
    """
    payload = url
    for key in sorted(set(params)):
        value = params[key]
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in sorted(set(values)):
            payload += f"{key}{item}"

    digest = hmac.new(
        auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("utf-8").strip()


def url_variants(url: str) -> list:
    """
    Twilio's own validator accepts the signature computed with OR without the
    default port, because their signing backend is inconsistent about it.
    Anything narrower risks rejecting genuine calls.
    """
    parsed = urlparse(url)
    default_port = 443 if parsed.scheme == "https" else 80

    if parsed.port and parsed.port == default_port:
        without = parsed._replace(netloc=parsed.netloc.split(":")[0]).geturl()
        return [url, without]
    if not parsed.port:
        with_port = parsed._replace(netloc=f"{parsed.netloc}:{default_port}").geturl()
        return [url, with_port]
    return [url]


def verify_twilio_request(request: Request, form: dict) -> bool:
    """
    True when the signature matches. Raises 403 when it doesn't.

    If no auth token is configured there is nothing to verify against, so we
    allow the call through and shout about it — local development has no
    Twilio signature at all, and refusing everything would break the demo.
    """
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not token:
        logger.warning(
            "TWILIO_AUTH_TOKEN is not set — webhook signature checks are DISABLED. "
            "Set it before exposing this app publicly."
        )
        return True

    provided = request.headers.get(SIG_HEADER)
    if not provided:
        logger.warning(f"Rejected {request.url.path}: no {SIG_HEADER} header")
        raise HTTPException(403, "Missing Twilio signature")

    url = public_url_for(request)
    # constant-time compare so the signature can't be guessed byte by byte
    for candidate in url_variants(url):
        if hmac.compare_digest(expected_signature(candidate, form, token), provided):
            return True

    logger.warning(f"Rejected {request.url.path}: bad {SIG_HEADER}")
    raise HTTPException(403, "Invalid Twilio signature")