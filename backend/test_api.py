# backend/test_api.py
# ─────────────────────────────────────────────
# Owner  : Suhas
# Task   : Pytest suite — auth, business CRUD, voice webhooks,
#          CRM (leads/calls), outbound calling
#
# Auth note: endpoints under /api/* require a bearer token
# (see auth.py). Voice webhooks stay open for Twilio.
# LLM/TTS are stubbed in webhook tests to stay hermetic.
# Run:  cd backend && pytest test_api.py -v
# ─────────────────────────────────────────────

import os
import sys
import uuid
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import Boolean, Column, String

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from main import app  # noqa: E402

client = TestClient(app)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _make_user(email=None, name="Owner One", password="secret123"):
    # Unique email by default: the SQLite DB persists across tests
    email = email or f"user-{uuid.uuid4().hex[:8]}@test.com"
    r = client.post("/api/auth/signup", json={"email": email, "name": name, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _tw(path, data=None):
    """POST to a /twilio/* webhook the way Twilio really does — with a valid signature."""
    from twilio_security import expected_signature
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    headers = {}
    if token:
        headers["X-Twilio-Signature"] = expected_signature(
            f"http://testserver{path}", data or {}, token
        )
    return client.post(path, data=data, headers=headers)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_business(token, name="Premier Motors BMW", language="en", filename="bmw.txt", content=None):
    content = content or "BMW 3 Series: Rs 62 lakh\nX5: Rs 1.05 crore\nHours: 10AM-8PM Mon-Sat"
    r = client.post(
        "/api/business",
        data={"name": name, "language": language},
        files={"file": (filename, content.encode())},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


# ─────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────

def test_health_check():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "running"


# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────

def test_signup_login_me():
    data = _make_user()
    email = data["user"]["email"]
    assert data["success"] is True
    assert data["token"]

    # login with same credentials
    r = client.post("/api/auth/login", json={"email": email, "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["token"]

    # me resolves the token
    r = client.get("/api/auth/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["email"] == email


def test_signup_duplicate_email():
    email = f"dup-{uuid.uuid4().hex[:8]}@test.com"
    _make_user(email=email)
    r = client.post("/api/auth/signup", json={"email": email, "name": "X", "password": "secret123"})
    assert r.status_code == 409


def test_signup_short_password():
    r = client.post("/api/auth/signup", json={"email": f"short-{uuid.uuid4().hex[:8]}@test.com", "name": "X", "password": "123"})
    assert r.status_code == 400


def test_login_wrong_password():
    email = f"wrongpw-{uuid.uuid4().hex[:8]}@test.com"
    _make_user(email=email)
    r = client.post("/api/auth/login", json={"email": email, "password": "nope-nope"})
    assert r.status_code == 401


# ─────────────────────────────────────────────
# Business endpoints (auth-protected)
# ─────────────────────────────────────────────

def test_create_business_requires_auth():
    r = client.post(
        "/api/business",
        data={"name": "No Auth Store", "language": "en"},
        files={"file": ("x.txt", b"hello")},
    )
    assert r.status_code == 401


def test_businesses_list_requires_auth():
    assert client.get("/api/businesses").status_code == 401
    assert client.get("/api/analytics/overview").status_code == 401


def test_create_business_success():
    data = _make_user()
    biz = _make_business(data["token"])
    assert biz["success"] is True
    assert biz["business_id"]
    assert "/twilio/answer/" in biz["voice_webhook_url"]


def test_owner_sees_only_own_businesses():
    a = _make_user(name="A")
    b = _make_user(name="B")

    _make_business(a["token"], name="A Showroom")
    _make_business(b["token"], name="B Finance")

    ra = client.get("/api/businesses", headers=_auth(a["token"])).json()
    rb = client.get("/api/businesses", headers=_auth(b["token"])).json()

    names_a = [x["name"] for x in ra["businesses"]]
    names_b = [x["name"] for x in rb["businesses"]]
    assert names_a == ["A Showroom"]
    assert names_b == ["B Finance"]


def test_owner_isolation_on_get():
    a = _make_user(name="IsoA")
    b = _make_user(name="IsoB")
    biz = _make_business(a["token"], name="Secret Agent")

    # B cannot fetch A's agent
    r = client.get(f"/api/business/{biz['business_id']}", headers=_auth(b["token"]))
    assert r.status_code == 403

    # A can
    r = client.get(f"/api/business/{biz['business_id']}", headers=_auth(a["token"]))
    assert r.status_code == 200
    assert r.json()["name"] == "Secret Agent"


def test_create_business_invalid_language():
    data = _make_user()
    r = client.post(
        "/api/business",
        data={"name": "Test", "language": "fr"},
        files={"file": ("x.txt", b"hello")},
        headers=_auth(data["token"]),
    )
    assert r.status_code == 400


def test_create_business_missing_name():
    data = _make_user()
    r = client.post(
        "/api/business",
        data={"language": "en"},
        files={"file": ("x.txt", b"hello")},
        headers=_auth(data["token"]),
    )
    assert r.status_code == 400


def test_analytics_scoped_to_owner():
    a = _make_user(name="Ana")
    _make_business(a["token"], name="Ana Motors")
    d = client.get("/api/analytics/overview", headers=_auth(a["token"])).json()
    assert d["total_businesses"] == 1


# ─────────────────────────────────────────────
# Voice webhooks (open, LLM/TTS stubbed)
# ─────────────────────────────────────────────

def _register_business(name="Voice Test Store", language="gu"):
    data = _make_user(name="Voice Tester")
    return _make_business(data["token"], name=name, language=language)["business_id"]


# ─────────────────────────────────────────────
# Wizard knowledge base + CRM
# ─────────────────────────────────────────────

def test_create_business_with_wizard_answers_only():
    """Document is optional — wizard answers alone build the knowledge base"""
    data = _make_user(name="Wizard Owner")
    r = client.post(
        "/api/business",
        data={
            "name": "Sharma Automobiles",
            "language": "en",
            "category": "showroom",
            "city": "SG Highway, Ahmedabad",
            "hours": "Mon-Sat 10am to 8pm",
            "products": "BMW 3 Series - Rs 62 lakh\nBMW X1 - Rs 49 lakh",
            "extra": "Free test drive with 1 day notice",
        },
        headers=_auth(data["token"]),
    )
    assert r.status_code == 200, r.text
    biz = r.json()
    assert biz["success"] is True
    # KB must contain the structured wizard data
    assert "Sharma Automobiles" in biz["knowledge_preview"]
    assert "62 lakh" in biz["knowledge_preview"]


def test_leads_require_auth_and_owner_isolation():
    assert client.get("/api/leads").status_code == 401
    assert client.get("/api/calls").status_code == 401

    a = _make_user(name="IsoA")
    b = _make_user(name="IsoB")
    biz = _make_business(a["token"], name="Iso Motors")
    # b has no leads
    assert client.get("/api/leads", headers=_auth(b["token"])).json()["total"] == 0


# ─────────────────────────────────────────────
# Outbound calling
# ─────────────────────────────────────────────

def test_phone_status_requires_auth():
    assert client.get("/api/phone/status").status_code == 401


def test_call_now_requires_auth():
    r = client.post("/api/call-now/whatever", json={"number": "9876543210"})
    assert r.status_code == 401


def test_call_now_invalid_number():
    data = _make_user(name="Dial Owner")
    biz = _make_business(data["token"], name="Dial Motors")
    r = client.post(
        f"/api/call-now/{biz['business_id']}",
        json={"number": "123"},
        headers=_auth(data["token"]),
    )
    assert r.status_code == 400


def test_call_now_without_provider_honest_failure():
    """Without provider keys, the API says so in plain language (and env may have keys — accept both)"""
    data = _make_user(name="Honest Owner")
    biz = _make_business(data["token"], name="Honest Motors")
    r = client.post(
        f"/api/call-now/{biz['business_id']}",
        json={"number": "9876500011", "name": "Rajesh", "info": "X5 exchange offer"},
        headers=_auth(data["token"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] in (True, False)
    assert body["detail"]  # always a human-friendly message
    # recorded in outbound history either way
    hist = client.get("/api/outbound", headers=_auth(data["token"])).json()
    assert hist["total"] == 1
    assert hist["calls"][0]["number"] == "9876500011"


def test_campaign_upload_csv_and_auth():
    data = _make_user(name="Campaign Owner")
    biz = _make_business(data["token"], name="Campaign Motors")
    bid = biz["business_id"]

    # auth required
    assert client.post(f"/api/campaigns/{bid}").status_code == 401

    csv_content = (
        "name,mobile,info\n"
        "Rajesh Kumar,9876500011,X5 exchange offer\n"
        "Priya Desai,9876500022,i4 test drive\n"
        "Bad Row,123,broken number\n"
    )
    r = client.post(
        f"/api/campaigns/{bid}",
        files={"file": ("list.csv", csv_content.encode())},
        headers=_auth(data["token"]),
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 2          # bad row skipped
    assert len(d["problems"]) == 1  # and reported
    assert d["calls"][0]["name"] == "Rajesh Kumar"

    # owner isolation
    other = _make_user(name="Other Owner")
    assert client.get(f"/api/campaigns/{d['campaign_id']}", headers=_auth(other["token"])).status_code == 403
    assert client.get(f"/api/campaigns/{d['campaign_id']}", headers=_auth(data["token"])).status_code == 200


def test_campaign_start_stops_cleanly_on_dial_failure(monkeypatch):
    """Campaign dialer stops cleanly with honest per-row reasons when dialing fails"""
    def failing_place_call(to_number, answer_url, from_number=None, status_callback=None):
        return {"ok": False, "call_uuid": None, "detail": "Twilio account has no calling credit left — top it up in the Twilio dashboard."}

    monkeypatch.setattr("main.place_call", failing_place_call)
    # The dialer falls back to a simulator when no provider is configured
    # (always true in tests). Pretend Twilio is wired so this exercises the
    # real-dial failure path the test is about.
    monkeypatch.setattr("main.twilio_configured", lambda: True)

    data = _make_user(name="Start Owner")
    biz = _make_business(data["token"], name="Start Motors")
    csv_content = "name,mobile,info\nA,9876500011,offer\nB,9876500022,offer\n"
    up = client.post(
        f"/api/campaigns/{biz['business_id']}",
        files={"file": ("l.csv", csv_content.encode())},
        headers=_auth(data["token"]),
    ).json()

    r = client.post(f"/api/campaigns/{up['campaign_id']}/start", headers=_auth(data["token"]))
    assert r.status_code == 200

    import time as _t
    det = None
    for _ in range(40):  # up to ~10s for the dialer thread
        _t.sleep(0.25)
        det = client.get(f"/api/campaigns/{up['campaign_id']}", headers=_auth(data["token"])).json()
        if det["status"] != "running":
            break
    assert det is not None and det["status"] == "completed"
    assert det["failed"] == det["total"]
    for c in det["calls"]:
        assert c["status"] == "failed"
        assert "credit" in c["detail"].lower()  # honest reason present


# ─────────────────────────────────────────────
# Twilio path (free-trial provider) — TwiML twins of the voice webhooks
# ─────────────────────────────────────────────

def test_twilio_answer_twin(monkeypatch):
    async def fake_tts(text, language):
        return "greet.mp3"
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    business_id = _register_business(language="hi")
    r = _tw(f"/twilio/answer/{business_id}")
    assert r.status_code == 200, r.text
    xml = r.text
    assert "<Response>" in xml and "<Play>" in xml
    assert "greet.mp3" in xml
    assert '<Gather input="speech"' in xml and 'language="hi-IN"' in xml
    assert f"/twilio/event/{business_id}" in xml


def test_twilio_event_twin_conversation(monkeypatch):
    seen = []

    def fake_agent_response(**kwargs):
        history = kwargs["conversation_history"]
        seen.append(kwargs["customer_query"])
        history.append({"role": "user", "content": kwargs["customer_query"]})
        history.append({"role": "assistant", "content": "jawab"})
        return "jawab"

    async def fake_tts(text, language):
        return f"{abs(hash(text)) % 99999}.mp3"

    monkeypatch.setattr("main.get_agent_response", fake_agent_response)
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    business_id = _register_business(language="en")

    # Twilio posts form-encoded webhooks
    r1 = _tw(
        f"/twilio/event/{business_id}",
        data={"CallSid": "CAtest123", "SpeechResult": "What is the price of the X5?"},
    )
    assert r1.status_code == 200, r1.text
    assert "<Play>" in r1.text and "<Gather" in r1.text
    assert seen == ["What is the price of the X5?"]

    # memory carries into turn 2
    r2 = _tw(
        f"/twilio/event/{business_id}",
        data={"CallSid": "CAtest123", "SpeechResult": "and test drive?"},
    )
    assert r2.status_code == 200
    assert seen == ["What is the price of the X5?", "and test drive?"]


def test_twilio_event_twin_silence_farewell(monkeypatch):
    async def fake_tts(text, language):
        return "f.mp3"
    monkeypatch.setattr("main.get_agent_response", lambda **kw: "ok")
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    business_id = _register_business(language="en")
    r1 = _tw(f"/twilio/event/{business_id}", data={"CallSid": "CAsil", "SpeechResult": ""})
    assert r1.status_code == 200
    assert "<Play>" in r1.text and "<Gather" in r1.text  # reprompt, keep listening

    r2 = _tw(f"/twilio/event/{business_id}", data={"CallSid": "CAsil", "SpeechResult": ""})
    assert r2.status_code == 200
    assert "<Hangup/>" in r2.text  # second silence → farewell + hangup


def test_twilio_event_twin_saves_lead(monkeypatch):
    import main as main_mod

    async def fake_tts(text, language):
        return "l.mp3"

    def fake_agent_response(**kwargs):
        history = kwargs["conversation_history"]
        history.append({"role": "user", "content": kwargs["customer_query"]})
        history.append({"role": "assistant", "content": "Sure ji, may I have your number?"})
        return "Sure ji, may I have your number?"

    monkeypatch.setattr("main.get_agent_response", fake_agent_response)
    monkeypatch.setattr("main.synthesize_speech", fake_tts)
    monkeypatch.setattr(
        main_mod,
        "extract_lead_info",
        lambda history: {
            "caller_name": "Twilio Caller",
            "caller_number": "9876543210",
            "interest": "test drive",
            "intent": "test_drive",
            "scheduled_for": "",
            "notes": "via Twilio twin",
        },
    )

    data = _make_user(name="Twilio CRM Owner")
    biz = _make_business(data["token"], name="Twilio Motors")
    bid = biz["business_id"]
    _tw(f"/twilio/event/{bid}", data={"CallSid": f"CA{bid}", "SpeechResult": "I want to book a test drive"})
    _tw(f"/twilio/event/{bid}", data={"CallSid": f"CA{bid}", "SpeechResult": "my number is 9876543210"})
    _tw(f"/twilio/event/{bid}", data={"CallSid": f"CA{bid}", "SpeechResult": ""})  # silence 1 → reprompt
    r4 = _tw(f"/twilio/event/{bid}", data={"CallSid": f"CA{bid}", "SpeechResult": ""})  # silence 2 → wrap up + save lead
    assert r4.status_code == 200
    assert "<Hangup/>" in r4.text

    leads = client.get("/api/leads", headers=_auth(data["token"])).json()["leads"]
    assert len(leads) == 1
    assert leads[0]["caller_name"] == "Twilio Caller"


def test_phone_status_reports_provider():
    assert client.get("/api/phone/status").status_code == 401  # auth still required


def test_twilio_place_call_form(monkeypatch):
    """Twilio dialer posts E.164 form fields with Basic auth"""
    import phone_service

    captured = {}

    class FakeResp:
        status_code = 201
        content = b'{"sid": "CAfake123"}'
        def json(self):
            return {"sid": "CAfake123"}

    def fake_post(url, data=None, auth=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["auth"] = auth
        return FakeResp()

    monkeypatch.setattr(phone_service.requests, "post", fake_post)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15551234567")

    res = phone_service.place_call(to_number="918200344429", answer_url="https://x.loca.lt/twilio/answer/abc")
    assert res["ok"] is True and res["call_uuid"] == "CAfake123"
    assert captured["data"]["To"] == "+918200344429"
    assert captured["data"]["From"] == "+15551234567"
    assert captured["auth"] == ("ACtest123", "tok")
    assert "Calls.json" in captured["url"]


def test_place_call_refuses_loopback_answer_url(monkeypatch):
    """A localhost answer URL is unreachable from Twilio's servers — refuse it
    with an actionable sentence instead of paying Twilio to reject the dial.
    (Regression: this surfaced as 'Url is not a valid URL' on the dashboard.)"""
    import phone_service

    def fail_if_called(*a, **k):
        raise AssertionError("place_call must not contact Twilio for a loopback URL")

    monkeypatch.setattr(phone_service.requests, "post", fail_if_called)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15551234567")

    for url in (
        "http://localhost:8000/twilio/answer/d0011610",
        "http://127.0.0.1:8000/twilio/answer/abc",
        "http://[::1]:8000/twilio/answer/abc",
    ):
        res = phone_service.place_call(to_number="918200344429", answer_url=url)
        assert res["ok"] is False, url
        assert "localhost" in res["detail"]
        assert "start_tunnel" in res["detail"]
        # never leak the raw Twilio wording that sent the owner hunting
        assert "not a valid URL" not in res["detail"]


def test_twilio_unverified_number_humanized(monkeypatch):
    import phone_service

    class FakeResp:
        status_code = 400
        content = b'{"code": "21215", "message": "Number is not verified for trial account"}'
        def json(self):
            return {"code": 21215, "message": "Number is not verified for trial account"}

    monkeypatch.setattr(phone_service.requests, "post", lambda *a, **kw: FakeResp())
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15551234567")

    res = phone_service.place_call(to_number="918200344429", answer_url="https://x/twilio/answer/abc")
    assert res["ok"] is False
    assert "verif" in res["detail"].lower()


def test_twilio_status_saves_lead_on_hangup(monkeypatch):
    """Caller hangs up mid-conversation → status callback still extracts the lead"""
    import main as main_mod

    def fake_agent_response(**kwargs):
        history = kwargs["conversation_history"]
        history.append({"role": "user", "content": kwargs["customer_query"]})
        history.append({"role": "assistant", "content": "Noted ji"})
        return "Noted ji"

    async def fake_tts(text, language):
        return "h.mp3"

    monkeypatch.setattr("main.get_agent_response", fake_agent_response)
    monkeypatch.setattr("main.synthesize_speech", fake_tts)
    monkeypatch.setattr(
        main_mod,
        "extract_lead_info",
        lambda history: {
            "caller_name": "Poojan",
            "caller_number": "918200344429",
            "interest": "Audi A8 test drive Tuesday 10am",
            "intent": "test_drive",
            "scheduled_for": "Tuesday 10am",
            "notes": "wants brochure",
        },
    )

    data = _make_user(name="Hangup Owner")
    biz = _make_business(data["token"], name="Hangup Motors")
    bid = biz["business_id"]
    csid = f"CAHANGUP{bid}"
    _tw(f"/twilio/event/{bid}", data={"CallSid": csid, "SpeechResult": "book a test drive please"})
    _tw(f"/twilio/event/{bid}", data={"CallSid": csid, "SpeechResult": "my number is 9876543210"})
    # caller hangs up — no farewell ever runs
    r = _tw(f"/twilio/status/{bid}", data={"CallSid": csid, "CallStatus": "completed"})
    assert r.status_code == 200, r.text

    leads = client.get("/api/leads", headers=_auth(data["token"])).json()["leads"]
    assert len(leads) == 1
    assert leads[0]["caller_name"] == "Poojan"
    assert "Tuesday" in (leads[0].get("scheduled_for") or "")


def test_twilio_status_ignores_noncompleted():
    data = _make_user(name="NoLead Owner")
    biz = _make_business(data["token"], name="NoLead Motors")
    r = _tw(f"/twilio/status/{biz['business_id']}", data={"CallSid": "CAnone", "CallStatus": "no-answer"})
    assert r.status_code == 200
    leads = client.get("/api/leads", headers=_auth(data["token"])).json()["leads"]
    assert len(leads) == 0


def test_outbound_closes_orphaned_calling_rows():
    """A 'calling' row whose callback never arrived must not stay 'calling' forever.

    The console decides whether to tick its live-call slip off status, so a row
    stranded by a lost callback would show a call from last week as still up.
    """
    from datetime import datetime, timedelta
    import main
    from database import SessionLocal
    from models import OutboundCall

    data = _make_user(name="Orphan Owner")
    bid = _make_business(data["token"], name="Orphan Motors")["business_id"]

    session = SessionLocal()
    # the SQLite file persists between runs, so ids must be unique per run
    fresh_id, stale_id = f"fresh{uuid.uuid4().hex[:8]}", f"stale{uuid.uuid4().hex[:8]}"
    try:
        session.add_all([
            OutboundCall(id=fresh_id, business_id=bid, caller_number="9000000001",
                         status="calling", called_at=datetime.now()),
            OutboundCall(id=stale_id, business_id=bid, caller_number="9000000002",
                         status="calling",
                         called_at=datetime.now() - timedelta(seconds=main.CALL_WINDOW_SECONDS + 60)),
        ])
        session.commit()
    finally:
        session.close()

    calls = client.get("/api/outbound", headers=_auth(data["token"])).json()["calls"]
    by_id = {c["id"]: c for c in calls}
    assert by_id[stale_id]["status"] == "done"
    assert "no status update" in by_id[stale_id]["detail"]
    assert by_id[fresh_id]["status"] == "calling", "a call inside the window must stay live"


def test_outbound_turns_use_marketer_persona_not_receptionist(monkeypatch):
    """On an OUTBOUND call the customer didn't dial us, so Meera must introduce
    herself and pitch — not answer with the inbound 'the phone is ringing,
    how can I help you' receptionist prompt. The outbound-answer webhook seeds
    the reason-for-calling into the session; the event turn must send the
    OUTBOUND system prompt (with that reason) to the LLM."""
    import main
    from database import SessionLocal
    from models import OutboundCall

    data = _make_user(name="Persona Owner")
    bid = _make_business(data["token"], name="Persona Motors")["business_id"]

    oc_id = f"pers{uuid.uuid4().hex[:8]}"
    session = SessionLocal()
    try:
        session.add(OutboundCall(
            id=oc_id, business_id=bid, caller_name="Rajesh",
            caller_number="9876500099", info="the X5 exchange offer",
            status="pending",
        ))
        session.commit()
    finally:
        session.close()

    async def fake_tts(text, language):
        return "persona.mp3"
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    # Capture every system prompt the LLM is asked to use.
    captured_system_prompts = []

    def fake_create(**kwargs):
        msgs = kwargs.get("messages") or []
        if msgs and msgs[0].get("role") == "system":
            captured_system_prompts.append(msgs[0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Hello ji, this is Meera from Persona Motors."))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=fake_create)))

    with mock.patch("llm_service._get_client", return_value=fake_client):
        # 1. We call the customer → outbound-answer seeds the session + reason
        r_answer = _tw(f"/twilio/outbound-answer/{oc_id}")
        assert r_answer.status_code == 200, r_answer.text

        # 2. The customer replies → event turn must use the OUTBOUND persona
        r_event = _tw(
            f"/twilio/event/{bid}?oc={oc_id}",
            data={"CallSid": f"CA{oc_id}", "SpeechResult": "hello, who is this?"},
        )
        assert r_event.status_code == 200, r_event.text

    assert captured_system_prompts, "no LLM call captured"
    final_prompt = captured_system_prompts[-1]
    # The marketer persona, not the receptionist one:
    assert "OUTBOUND call" in final_prompt or "dialed the customer" in final_prompt, (
        f"outbound turn used the wrong persona: {final_prompt[:120]}")
    # It carries the specific reason pulled from the CSV 'info' column:
    assert "X5 exchange offer" in final_prompt
    # And it must NOT be the inbound receptionist prompt:
    assert "phone is ringing" not in final_prompt


def test_inbound_turns_keep_receptionist_persona(monkeypatch):
    """A customer calling US must still get the inbound receptionist persona —
    the outbound switch must not leak into inbound calls."""
    import main

    data = _make_user(name="Inbound Persona Owner")
    bid = _make_business(data["token"], name="Inbound Motors")["business_id"]

    async def fake_tts(text, language):
        return "in.mp3"
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    captured = []

    def fake_create(**kwargs):
        msgs = kwargs.get("messages") or []
        if msgs and msgs[0].get("role") == "system":
            captured.append(msgs[0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Certainly, how can I help you today?"))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=fake_create)))

    with mock.patch("llm_service._get_client", return_value=fake_client):
        r = _tw(f"/twilio/event/{bid}",
                data={"CallSid": "CAinbound1", "SpeechResult": "do you have the X5?"})
        assert r.status_code == 200, r.text

    assert captured, "no LLM call captured"
    assert "phone is ringing" in captured[-1], "inbound call lost its receptionist persona"
    assert "OUTBOUND call" not in captured[-1]


def test_migration_adds_detail_columns_to_an_existing_table():
    """create_all() never adds a column to a table that already exists —
    without migrate_missing_columns() every leads query would throw."""
    from sqlalchemy import inspect, text
    import database
    from models import Base

    table = "leads_migration_probe"
    with database.engine.begin() as conn:
        # a stand-in for the old schema: only the pre-detail columns
        conn.execute(text(f'CREATE TABLE IF NOT EXISTS {table} (id VARCHAR(50) PRIMARY KEY)'))

    class Probe(Base):
        __tablename__ = table
        id = Column(String(50), primary_key=True)
        product = Column(String(255), nullable=True)
        needs_callback = Column(Boolean, default=False)

    assert database.migrate_missing_columns(tables=(Probe,)) == [f"{table}.product", f"{table}.needs_callback"]
    cols = {c["name"] for c in inspect(database.engine).get_columns(table)}
    assert {"product", "needs_callback"} <= cols
    # running again must not try to re-add them
    assert database.migrate_missing_columns(tables=(Probe,)) == []
    with database.engine.begin() as conn:
        conn.execute(text(f'DROP TABLE {table}'))


def test_leads_api_exposes_every_detail_column():
    from datetime import datetime
    from database import SessionLocal
    from models import Lead

    data = _make_user(name="Detail Owner")
    bid = _make_business(data["token"], name="Detail Motors")["business_id"]

    lead_id = f"det{uuid.uuid4().hex[:8]}"
    session = SessionLocal()
    try:
        session.add(Lead(
            id=lead_id, business_id=bid, caller_name="Rajesh Kumar",
            caller_number="9876543210", caller_email="rk@example.com",
            product="BMW X5", interest="test drive", intent="test_drive",
            visit_type="test_drive", visit_at="2026-10-04 11:00",
            scheduled_for="tomorrow 11 am", needs_callback=True,
            callback_reason="send the EMI sheet", requested_material="emi_sheet",
            status="new", created_at=datetime.now(),
        ))
        session.commit()
    finally:
        session.close()

    leads = client.get("/api/leads", headers=_auth(data["token"])).json()["leads"]
    row = next(l for l in leads if l["id"] == lead_id)
    assert row["caller_number"] == "9876543210"
    assert row["caller_email"] == "rk@example.com"
    assert row["product"] == "BMW X5"
    assert row["visit_type"] == "test_drive"
    assert row["visit_at"] == "2026-10-04 11:00"
    assert row["needs_callback"] is True
    assert row["requested_material"] == "emi_sheet"


def test_extractor_fills_details_without_the_llm():
    """The Groq call can fail (no key, no network). The regex pass must still
    yield the contact and visit details on its own."""
    from llm_service import extract_lead_info

    history = [
        {"role": "user", "content": "Do you have the BMW X5? I can come for a test drive."},
        {"role": "assistant", "content": "Yes ji. What is your number?"},
        {"role": "user", "content": "98765 43210, and send me the brochure to rk@example.com"},
    ]
    with mock.patch("llm_service._get_client", side_effect=RuntimeError("no network")):
        info = extract_lead_info(history)

    assert info["caller_number"] == "9876543210"
    assert info["caller_email"] == "rk@example.com"
    assert info["visit_type"] == "test_drive"
    assert info["requested_material"] == "brochure"
    assert info["needs_callback"] is False


def test_extractor_rejects_a_nonsense_visit_at():
    from llm_service import extract_lead_info

    history = [{"role": "user", "content": "I can come tomorrow morning."}]
    fake = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content='{"visit_at": "sometime soon please", "product": "X5"}'))])
    with mock.patch("llm_service._get_client", return_value=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: fake)))):
        info = extract_lead_info(history)

    assert info["visit_at"] is None, "an unresolvable date must not reach the table"
    assert info["product"] == "X5"


def test_apply_lead_details_never_overwrites_a_good_answer():
    from database import apply_lead_details
    from models import Lead

    lead = Lead(id="x", business_id="b", product="BMW X5", needs_callback=True)
    apply_lead_details(lead, {"product": "BMW X3", "caller_email": "new@example.com",
                              "needs_callback": False, "visit_type": "showroom"})
    assert lead.product == "BMW X5", "an existing answer is stronger than a re-extraction"
    assert lead.needs_callback is True
    assert lead.caller_email == "new@example.com", "blanks should still be filled"
    assert lead.visit_type == "showroom"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])


# ── Dialling numbers the owner actually types ──────────────────────────────

def test_to_e164_adds_default_country_code():
    """'98765 43210' is what the portal's own placeholder suggests — it must work."""
    from phone_service import to_e164
    assert to_e164("98765 43210") == "+919876543210"
    assert to_e164("9876543210") == "+919876543210"
    assert to_e164("(98765) 432-10") == "+919876543210"


def test_to_e164_leaves_country_coded_numbers_alone():
    """Adding +91 to a number that already has it would produce a wrong number."""
    from phone_service import to_e164
    assert to_e164("+91 98765 43210") == "+919876543210"
    assert to_e164("919876543210") == "+919876543210"
    assert to_e164("+1 415 555 1234") == "+14155551234"
    assert to_e164("+44 20 7946 0958") == "+442079460958"


def test_to_e164_respects_configured_country(monkeypatch):
    from phone_service import to_e164
    monkeypatch.setenv("DEFAULT_COUNTRY_CODE", "1")
    assert to_e164("4155551234") == "+14155551234"
    assert to_e164("919876543210") == "+919876543210"  # already coded, untouched


def test_to_e164_rejects_junk():
    from phone_service import to_e164
    assert to_e164("") == ""
    assert to_e164("Rajesh Kumar") == ""
    assert to_e164("12345") == ""  # too short to be a number we should dial


def test_ten_digit_number_reaches_twilio_as_valid_e164(monkeypatch):
    """The regression: a bare 10-digit number was sent as '+9876543210' → Twilio error 21211."""
    import phone_service

    captured = {}

    class FakeResp:
        status_code = 201
        content = b'{"sid": "CAok"}'
        def json(self):
            return {"sid": "CAok"}

    monkeypatch.setattr(
        phone_service.requests, "post",
        lambda url, data=None, auth=None, timeout=None: (
            captured.update(data), FakeResp())[1],
    )
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15551234567")

    res = phone_service.place_call(to_number="98765 43210", answer_url="https://x/twilio/answer/a")
    assert res["ok"] is True
    assert captured["To"] == "+919876543210"


def test_campaign_csv_numbers_are_normalised():
    """Bulk upload accepts the same formats as the one-off box."""
    from phone_service import parse_call_list
    rows = [
        {"name": "Rajesh", "mobile": "98765 43210", "info": "X5"},
        {"name": "Priya", "number": "+91 98250 11223", "info": ""},
        {"name": "Bad", "mobile": "n/a", "info": ""},
    ]
    clean, problems = parse_call_list(rows)
    assert [r["number"] for r in clean] == ["+919876543210", "+919825011223"]
    assert len(problems) == 1 and "n/a" in problems[0]  # names the bad number


def test_humanizer_blames_the_customer_number_not_our_number():
    """21211 is a malformed 'To' — telling the owner to check TWILIO_PHONE_NUMBER is wrong."""
    from phone_service import _humanize_twilio_error as h
    msg = "The 'To' number: +9876543210 is not a valid phone number, shortcode, or alphanumeric sender ID."
    out = h("21211", msg)
    assert "TWILIO_PHONE_NUMBER" not in out
    assert "country code" in out.lower()


def test_humanizer_separates_from_and_trial_errors():
    from phone_service import _humanize_twilio_error as h
    from_num = h("21210", "The 'From' phone number +15673353544 is not a verified caller ID.")
    assert "verify the calling number" in from_num.lower()

    to_num = h("21219", "The 'To' number: +919876543210 is not a verified caller ID.")
    assert "on trial" in to_num.lower()
    assert "verify this customer's number" in to_num.lower()


def test_humanizer_keeps_real_unroutable_and_credit_wording():
    from phone_service import _humanize_twilio_error as h
    assert "can't be reached" in h("21216", "not a valid phone number for India")
    assert "credit" in h("20003", "Account balance is too low").lower()


def test_twilio_event_survives_an_empty_agent_reply(monkeypatch):
    """An empty LLM reply must not 500 the webhook — the caller would hear dead air."""
    import main as main_mod

    data = _make_user(name="Silent Owner")
    biz = _make_business(data["token"], name="Silent Motors")
    bid = biz["business_id"]

    seen = {}

    async def fake_tts(text, language):
        seen["text"] = text
        return "reprompt.mp3"

    monkeypatch.setattr("main.get_agent_response", lambda **kw: "")
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    r = _tw(f"/twilio/event/{bid}", data={"CallSid": "CA-silent-1", "SpeechResult": "hello?"})
    assert r.status_code == 200, f"webhook 500'd on an empty reply: {r.text[:200]}"
    assert "<Response>" in r.text and "<Play>" in r.text and "<Gather" in r.text
    # it spoke the shared reprompt instead of nothing
    assert seen["text"] and seen["text"].strip(), "TTS was handed empty text"


def test_twilio_event_still_speaks_a_real_reply(monkeypatch):
    """The guard must not swallow a genuine reply."""
    import main as main_mod

    data = _make_user(name="Talkative Owner")
    biz = _make_business(data["token"], name="Talkative Motors")
    bid = biz["business_id"]
    seen = {}

    async def fake_tts(text, language):
        seen["text"] = text
        return "answer.mp3"

    monkeypatch.setattr("main.get_agent_response", lambda **kw: "Our timings are 9 to 7.")
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    r = _tw(f"/twilio/event/{bid}", data={"CallSid": "CA-talk-1", "SpeechResult": "timings?"})
    assert r.status_code == 200, r.text
    assert seen["text"] == "Our timings are 9 to 7."


# ── Webhook authenticity (VULN-003) ───────────────────────────────────────
# Cross-checked byte-for-byte against twilio-python's RequestValidator.

def test_signature_matches_twilios_own_algorithm():
    """If this drifts from Twilio, every genuine call gets rejected."""
    from twilio_security import expected_signature
    assert expected_signature(
        "https://mycompany.com/myapp",
        {
            "CallSid": "CA1234567890ABCDE",
            "Caller": "+12349013030",
            "Digits": "1234",
            "From": "+12349013030",
            "To": "+18005551212",
        },
        "12345",
    ) == "3KI2uRuYyAdhZIJXcpU0izDUzWI="


def test_signature_accepts_url_with_or_without_default_port():
    """Twilio's signing backend is inconsistent about :443 — accept both."""
    from twilio_security import url_variants
    assert "https://mycompany.com/myapp" in url_variants("https://mycompany.com/myapp")
    assert "https://mycompany.com:443/myapp" in url_variants("https://mycompany.com/myapp")
    assert "https://h.co/a" in url_variants("https://h.co:443/a")


def test_unsigned_webhook_is_rejected():
    """The attack this closes: anyone POSTing a fake speech result."""
    business_id = _register_business()
    r = client.post(
        f"/twilio/event/{business_id}",
        data={"CallSid": "CAforged", "SpeechResult": "I want a test drive"},
    )
    assert r.status_code == 403, f"forged webhook was accepted: {r.status_code}"


def test_webhook_with_a_forged_signature_is_rejected():
    business_id = _register_business()
    r = client.post(
        f"/twilio/event/{business_id}",
        data={"CallSid": "CAforged2", "SpeechResult": "buy a car"},
        headers={"X-Twilio-Signature": "totally-made-up-signature"},
    )
    assert r.status_code == 403, f"bad signature accepted: {r.status_code}"


def test_signature_from_a_different_url_is_rejected():
    """A captured signature replayed against another path must not work."""
    from twilio_security import expected_signature
    business_id = _register_business()
    data = {"CallSid": "CAreplay", "SpeechResult": "hello"}
    stolen = expected_signature("http://testserver/somewhere/else", data,
                                os.getenv("TWILIO_AUTH_TOKEN", ""))
    r = client.post(
        f"/twilio/event/{business_id}", data=data,
        headers={"X-Twilio-Signature": stolen},
    )
    assert r.status_code == 403, f"replayed signature accepted: {r.status_code}"


def test_every_twilio_webhook_rejects_unsigned_posts():
    """No endpoint may be left open — that is how leads got injected."""
    business_id = _register_business()
    for path in (
        f"/twilio/answer/{business_id}",
        f"/twilio/outbound-answer/whatever",
        f"/twilio/event/{business_id}",
        f"/twilio/status/{business_id}",
    ):
        r = client.post(path, data={"CallSid": "CAopen"})
        assert r.status_code == 403, f"{path} accepted an unsigned POST ({r.status_code})"


# ── Postgres readiness (cloud deploys have an ephemeral disk) ──────────────

def test_render_style_postgres_url_is_normalised():
    """Render hands back postgres://; SQLAlchemy 2.x only speaks postgresql://."""
    from database import normalise_url
    from sqlalchemy.engine import make_url
    url = normalise_url("postgres://u:p@host:5432/voxagent")
    assert url == "postgresql://u:p@host:5432/voxagent"
    dialect = make_url(url).get_dialect()
    assert dialect.name == "postgresql"
    assert "postgresql" in dialect.__module__


def test_normalise_url_leaves_sqlite_and_postgres_alone():
    from database import normalise_url
    assert normalise_url("sqlite:///./x.db") == "sqlite:///./x.db"
    assert normalise_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


def test_postgres_engine_builds_without_sqlite_only_connect_args():
    """check_same_thread is a SQLite argument — Postgres rejects it at connect time."""
    from sqlalchemy import create_engine
    from database import normalise_url
    # engine creation is lazy, so this proves the URL + kwargs are accepted
    # without needing a live server
    eng = create_engine(normalise_url("postgres://u:p@h:5432/voxagent"),
                        connect_args={}, pool_pre_ping=True)
    assert eng.dialect.name == "postgresql"


def test_every_model_compiles_to_postgres_ddl():
    """A length-less String or SQLite-only type would only fail once deployed."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    from models import Base

    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert table.name.upper() in ddl.upper(), f"{table.name} produced no DDL"
        for col in table.columns:
            col.type.compile(dialect=dialect)


def test_migration_script_refuses_a_sqlite_target():
    """It must never be a no-op that looks like it worked."""
    import subprocess, sys
    out = subprocess.run(
        [sys.executable, "migrate_to_postgres.py", "--target", "sqlite:///./x.db"],
        capture_output=True, text=True,
    )
    assert out.returncode != 0
    assert "SQLite" in (out.stdout + out.stderr)


# ─────────────────────────────────────────────────────────────
# Tier-1 hardening: a webhook must never 5xx — a 5xx makes Twilio
# play "application error" and hang up on a live caller.
# ─────────────────────────────────────────────────────────────

def _broken_tts(text, language):
    raise RuntimeError("edge tts is down (simulated)")


def test_twilio_answer_survives_tts_failure(monkeypatch):
    """synthesize_speech raising must still yield 200 + valid TwiML."""
    import xml.etree.ElementTree as ET

    async def boom(text, language):
        _broken_tts(text, language)
    monkeypatch.setattr("main.synthesize_speech", boom)

    business_id = _register_business(language="hi")
    r = _tw(f"/twilio/answer/{business_id}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/xml")

    root = ET.fromstring(r.text)  # raises if the TwiML is malformed
    assert root.tag == "Response"
    say = root.find("Say")
    assert say is not None and (say.text or "").strip()
    assert say.get("language") == "hi-IN"  # apology spoken in the call's language


def test_twilio_event_speaks_the_same_text_when_tts_dies(monkeypatch):
    """The <Say> fallback carries the exact text we were going to play."""
    import xml.etree.ElementTree as ET

    async def boom(text, language):
        _broken_tts(text, language)
    monkeypatch.setattr("main.synthesize_speech", boom)
    monkeypatch.setattr("main.get_agent_response", lambda **kw: "नमस्ते, दाम जानना है")

    business_id = _register_business(language="hi")
    r = _tw(f"/twilio/event/{business_id}", data={"CallSid": "CAttsdown", "SpeechResult": "Price?"})
    assert r.status_code == 200, r.text
    root = ET.fromstring(r.text)
    say = root.find("Say")
    assert say is not None
    assert "नमस्ते" in (say.text or "")  # the agent's reply, spoken instead of played
    assert root.find("Gather") is not None  # the call keeps going
    assert root.find("Play") is None


def test_twilio_webhook_failure_returns_200_apology(monkeypatch):
    """Any unexpected exception inside the handler degrades to spoken apology."""
    import xml.etree.ElementTree as ET

    def explode(*args, **kwargs):
        raise RuntimeError("simulated database explosion")
    monkeypatch.setattr("main.get_business_record", explode)

    r = _tw("/twilio/answer/biz-that-cannot-load")
    assert r.status_code == 200, r.status_code
    root = ET.fromstring(r.text)
    assert root.tag == "Response"
    say = root.find("Say")
    assert say is not None and (say.text or "").strip()
    assert say.get("language") == "en-US"  # unknown language → English apology


def test_public_url_probe_retries_until_green(monkeypatch, caplog):
    """One flaky attempt must not report tunnel drift for a live tunnel."""
    import logging as _logging
    import main

    calls = []

    def flaky_head(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("simulated connect timeout")
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("main.requests.head", flaky_head)
    monkeypatch.setattr("main.time.sleep", lambda s: None)
    with caplog.at_level(_logging.INFO, logger="main"):
        main._check_public_url()

    assert len(calls) == 2  # stopped retrying the moment it went green
    assert any("PUBLIC_BASE_URL reachable" in r.getMessage() for r in caplog.records)
    assert not any("is NOT reachable" in r.getMessage() for r in caplog.records)


def test_public_url_probe_gives_up_loudly_after_retries(monkeypatch, caplog):
    """A stale tunnel URL answers 503 every time — warn after exactly N tries."""
    import logging as _logging
    import main

    calls, sleeps = [], []

    def dead_head(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=503)  # localtunnel "Tunnel Unavailable"

    monkeypatch.setattr("main.requests.head", dead_head)
    monkeypatch.setattr("main.time.sleep", lambda s: sleeps.append(s))
    with caplog.at_level(_logging.WARNING, logger="main"):
        main._check_public_url()

    assert len(calls) == main.PUBLIC_URL_PROBE_ATTEMPTS
    assert sleeps == [main.PUBLIC_URL_PROBE_PAUSE] * (main.PUBLIC_URL_PROBE_ATTEMPTS - 1)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == _logging.WARNING]
    assert any("is NOT reachable after 3 attempts" in w for w in warnings), warnings


def test_public_url_probe_is_deferred_past_socket_bind(monkeypatch):
    """The probe runs AFTER boot: during the startup event uvicorn's socket
    isn't accepting yet, so a localhost PUBLIC_BASE_URL could never answer
    and a healthy local run would be flagged as tunnel drift."""
    import asyncio
    import main

    calls = []
    monkeypatch.setattr(main, "_check_public_url", lambda: calls.append("probed"))
    asyncio.run(main._probe_public_url_after_boot(delay=0))
    assert calls == ["probed"]  # deferred wrapper still runs the probe

