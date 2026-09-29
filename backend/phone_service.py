# backend/phone_service.py
# ─────────────────────────────────────────────
# Task   : Outbound dialing via the Vonage Voice REST API.
#
# Auth: the Voice API requires a signed JWT (application ID + private
# key), NOT the account key/secret (those are valid for the account
# APIs only). We auto-created the Vonage application and key.
#
# Honest failure: every failure returns a human-friendly detail —
# the dashboard shows it in plain words instead of failing silently.
# ─────────────────────────────────────────────
import os
import time
import secrets
import logging

import requests
import jwt as pyjwt

logger = logging.getLogger(__name__)

VONAGE_REST_URL = "https://api.nexmo.com/v1/calls"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _application_id() -> str:
    return os.getenv("VONAGE_APPLICATION_ID", "")


def _private_key() -> str:
    path = os.getenv("VONAGE_PRIVATE_KEY_PATH", "")
    if path and os.path.isabs(path):
        full = path
    elif path:
        full = os.path.join(BASE_DIR, path)
    else:
        full = os.path.join(BASE_DIR, "vonage_private.key")
    try:
        with open(full, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _jwt() -> str:
    """Sign a short-lived Vonage Voice JWT"""
    app_id = _application_id()
    now = int(time.time())
    payload = {
        "iss": app_id,
        "application_id": app_id,
        "iat": now,
        "exp": now + 900,
        "jti": secrets.token_hex(16),
    }
    return pyjwt.encode(payload, _private_key(), algorithm="RS256")


def vonage_configured() -> bool:
    return bool(
        os.getenv("VONAGE_API_KEY")
        and os.getenv("VONAGE_API_SECRET")
        and _application_id()
        and _private_key()
        and os.getenv("VONAGE_PHONE_NUMBER")
    )


def place_call(
    to_number: str,
    answer_url: str,
    from_number: str = None,
) -> dict:
    """
    Place an outbound call. The callee's phone bridges to answer_url,
    which returns the NCCO (Meera speaking).
    Returns {"ok": bool, "call_uuid": str|None, "detail": str}
    where detail is always human-friendly.
    """
    if not vonage_configured():
        missing = []
        if not _application_id() or not _private_key():
            missing.append("Vonage application keys")
        if not os.getenv("VONAGE_PHONE_NUMBER"):
            missing.append("a Vonage phone number")
        return {
            "ok": False,
            "call_uuid": None,
            "detail": "Phone calling isn't connected yet — " + " and ".join(missing) + " are missing from the settings.",
        }

    body = {
        "to": [{"type": "phone", "number": to_number}],
        "from": {"type": "phone", "number": (from_number or os.getenv("VONAGE_PHONE_NUMBER", "")).replace("+", "")},
        "answer_url": [answer_url],
        "answer_method": "POST",
    }

    try:
        resp = requests.post(
            VONAGE_REST_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {_jwt()}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code in (200, 201):
            uuid = (data.get("uuid") or data.get("conversation_uuid") or "")
            logger.info(f"📞 Outbound call placed to {to_number}: {uuid}")
            return {"ok": True, "call_uuid": uuid, "detail": "Call started"}
        # Honest, plain-language failures
        detail = data.get("detail") or data.get("message") or f"Vonage returned {resp.status_code}"
        logger.warning(f"Outbound call to {to_number} failed: {detail}")
        return {"ok": False, "call_uuid": None, "detail": _humanize_vonage_error(detail, resp.status_code)}
    except Exception as e:
        logger.error(f"Outbound call error: {e}")
        return {"ok": False, "call_uuid": None, "detail": "Could not reach the phone network. Check your internet or Vonage settings."}


def _humanize_vonage_error(detail: str, status: int) -> str:
    d = (detail or "").lower()
    if status == 401:
        return "Vonage rejected the application credentials — check VONAGE_APPLICATION_ID and the private key."
    if "from" in d and ("invalid" in d or "not valid" in d or "owned" in d):
        return "Your Vonage number isn't valid or rented yet — rent a number in the Vonage dashboard."
    if "quota" in d or "credit" in d or "balance" in d or "insufficient" in d:
        return "Vonage account has no calling credit left — top it up in the Vonage dashboard."
    if "invalid destination" in d or "unroutable" in d or "not authorised" in d:
        return "This phone number can't be called from your account (trial accounts can only call verified numbers)."
    return f"Phone network said: {detail}"


def parse_call_list(rows: list) -> tuple[list, list]:
    """
    Normalize Excel/CSV rows into {name, number, info}.
    Accepts flexible column names (name/nam, mobile/phone/number/contact, info/note/remark).
    Returns (clean_rows, problems) where problems are human-friendly strings.
    """
    clean, problems = [], []
    for i, row in enumerate(rows, start=2):  # Excel rows start at 1, header at 1
        low = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
        name = low.get("name") or low.get("nam") or low.get("customer") or low.get("client") or ""
        number = (
            low.get("mobile") or low.get("number") or low.get("phone")
            or low.get("contact") or low.get("mobile number") or low.get("phone number") or ""
        )
        info = low.get("info") or low.get("note") or low.get("notes") or low.get("remark") or low.get("remarks") or low.get("about") or ""
        name = str(name).strip() if name is not None else ""
        number = str(number).strip() if number is not None else ""
        info = str(info).strip() if info is not None else ""

        # digits-only check (allow leading +)
        digits = number.replace(" ", "").replace("-", "").replace("+", "")
        if not digits.isdigit() or len(digits) < 10:
            problems.append(f"Row {i}: '{number or 'empty'}' doesn't look like a phone number — skipped")
            continue
        clean.append({"name": name, "number": number, "info": info})
    return clean, problems
