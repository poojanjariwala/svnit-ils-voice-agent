# backend/phone_service.py
# ─────────────────────────────────────────────
# Task   : Outbound dialing via the Twilio Voice REST API.
#
# Twilio is called over plain HTTPS with Basic auth — no SDK needed.
#
# Honest failure: every failure returns a human-friendly detail —
# the dashboard shows it in plain words instead of failing silently.
# ─────────────────────────────────────────────
import os
import re
import logging
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

TWILIO_CALLS_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"


def to_e164(number: str) -> str:
    """
    Normalise whatever the owner typed into something Twilio will accept.

    People type "98765 43210" or "+91 98765 43210" — both should work.
    A bare 10-digit national number gets DEFAULT_COUNTRY_CODE (default 91,
    India) prepended; anything already country-coded is left alone.
    Returns "" for anything too short or not a number at all.
    """
    raw = re.sub(r"[^0-9+]", "", str(number or ""))
    if raw.startswith("+"):
        raw = raw[1:]
    if not raw.isdigit() or len(raw) < 7:
        # Nothing diallable here — names, "n/a", stray punctuation, short codes.
        return ""
    cc = re.sub(r"\D", "", os.getenv("DEFAULT_COUNTRY_CODE", "91"))
    # Already country-coded, e.g. 919876543210 or 14155551234.
    if cc and raw.startswith(cc) and len(raw) > 10:
        return f"+{raw}"
    # A bare national number, e.g. 9876543210.
    if len(raw) == 10 and cc:
        return f"+{cc}{raw}"
    return f"+{raw}"


def twilio_configured() -> bool:
    return bool(
        os.getenv("TWILIO_ACCOUNT_SID", "").startswith("AC")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_PHONE_NUMBER", "").startswith("+")
    )


def _answer_url_is_fetchable(answer_url: str) -> bool:
    """
    Twilio fetches answer_url from ITS servers, so a loopback host
    (localhost/127.0.0.1/::1) is unreachable by construction. Catching it
    here turns Twilio's cryptic 'Url is not a valid URL' into a sentence the
    owner can act on, and skips a paid API call that is doomed to fail.
    """
    try:
        host = urlparse(answer_url).hostname or ""
    except ValueError:
        return False
    return host not in ("localhost", "127.0.0.1", "::1")


def place_call(
    to_number: str,
    answer_url: str,
    from_number: str = None,
    status_callback: str = None,
) -> dict:
    """
    Dial via Twilio's REST API (Basic auth, form-encoded). The callee's
    phone bridges to answer_url, which returns the TwiML script
    (Meera speaking).
    Returns {"ok": bool, "call_uuid": str|None, "detail": str}
    where detail is always human-friendly.
    """
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    # E.164 for both legs — "98765 43210" and "+919876543210" both work.
    to = to_e164(to_number)
    frm = to_e164(from_number or os.getenv("TWILIO_PHONE_NUMBER", ""))

    if not to:
        return {
            "ok": False,
            "call_uuid": None,
            "detail": "That doesn't look like a phone number.",
        }

    if not (sid and token and frm):
        return {
            "ok": False,
            "call_uuid": None,
            "detail": "Phone calling isn't connected yet — a Twilio number is missing from the settings.",
        }

    if not _answer_url_is_fetchable(answer_url):
        return {
            "ok": False,
            "call_uuid": None,
            "detail": (
                "This server is only reachable at localhost, so the phone network can't "
                "fetch the call script. Start the tunnel (python backend/start_tunnel.py) "
                "and try again."
            ),
        }

    form = {"To": to, "From": frm, "Url": answer_url}
    if status_callback:
        form["StatusCallback"] = status_callback

    try:
        resp = requests.post(
            TWILIO_CALLS_URL.format(sid=sid),
            data=form,
            auth=(sid, token),
            timeout=30,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code in (200, 201):
            csid = data.get("sid", "")
            logger.info(f"📞 Twilio outbound call placed to {to}: {csid}")
            return {"ok": True, "call_uuid": csid, "detail": "Call started"}
        code = str(data.get("code", ""))
        message = data.get("message") or f"Twilio returned {resp.status_code}"
        logger.warning(f"Twilio call to {to} failed ({code}): {message}")
        return {"ok": False, "call_uuid": None, "detail": _humanize_twilio_error(code, message)}
    except Exception as e:
        logger.error(f"Twilio call error: {e}")
        return {"ok": False, "call_uuid": None, "detail": "Could not reach the phone network. Check your internet or Twilio settings."}


def _humanize_twilio_error(code: str, message: str) -> str:
    """
    One honest sentence the owner can act on. Twilio's raw codes are useless
    to a car-dealer, and a wrong sentence sends them fixing the wrong thing —
    so the 'To' (customer) and 'From' (our number) cases are kept apart.
    """
    d = f"{code} {message}".lower()

    # 21210 — OUR number isn't verified as an outgoing caller ID.
    if code == "21210" or ("from" in d and "verif" in d):
        return ("The number Meera calls from isn't verified on this Twilio account — "
                "verify the calling number in the Twilio console (Phone Numbers → Verified Caller IDs).")

    # 21219 — trial account dialling a customer number nobody has verified.
    if code == "21219" or code == "21215" or ("trial" in d and "verif" in d):
        return ("This Twilio account is still on trial, so it can only call numbers that have been "
                "verified in the Twilio console — verify this customer's number first (it takes 30 seconds).")

    # 21211 — the number we dialled is malformed, almost always a missing country code.
    if code == "21211" or ("invalid" in d and "'to'" in d):
        return ("That number isn't in international format — enter it with its country code, "
                "for example +91 98765 43210.")

    # The From number is the wrong shape for this account.
    if ("from" in d and ("invalid" in d or "not yours" in d or "not owned" in d)):
        return "The number Meera calls from isn't valid for this Twilio account — check TWILIO_PHONE_NUMBER in the settings."

    if "balance" in d or "credit" in d or "quota" in d:
        return "Twilio account has no calling credit left — check your trial balance in the Twilio console."

    if "unroutable" in d or "invalid destination" in d or code == "21216":
        return "That phone number can't be reached from your Twilio account."

    if code == "20001" or ("authenticate" in d and "password" in d):
        return "Twilio rejected the account SID or auth token — check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in the settings."

    return f"Phone network said: {message}"


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

        # Store the E.164 form so the dialer sends something Twilio accepts.
        e164 = to_e164(number)
        if not e164 or len(e164.lstrip("+")) < 10:
            problems.append(f"Row {i}: '{number or 'empty'}' doesn't look like a phone number — skipped")
            continue
        clean.append({"name": name, "number": e164, "info": info})
    return clean, problems
