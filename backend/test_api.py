# backend/test_api.py
# ─────────────────────────────────────────────
# Owner  : Suhas
# Task   : Pytest suite — auth, business CRUD, voice webhooks,
#          CRM (leads/calls), outbound calling
#
# Auth note: endpoints under /api/* require a bearer token
# (see auth.py). Voice webhooks stay open for Vonage.
# LLM/TTS are stubbed in webhook tests to stay hermetic.
# Run:  cd backend && pytest test_api.py -v
# ─────────────────────────────────────────────

import os
import sys
import uuid

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
    assert "/voice/answer/" in biz["voice_webhook_url"]


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


def test_voice_answer_webhook(monkeypatch):
    async def fake_tts(text, language):
        return "greeting.mp3"
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    business_id = _register_business(language="gu")
    r = client.post(f"/voice/answer/{business_id}")
    assert r.status_code == 200
    ncco = r.json()
    assert isinstance(ncco, list)
    # Greeting is pre-synthesized audio in the SAME voice as answers
    assert ncco[0]["action"] == "play"
    assert ncco[0]["url"][0].endswith("greeting.mp3")
    assert ncco[1]["action"] == "input"          # then listens for speech
    assert business_id in ncco[1]["eventUrl"][0]


def test_voice_answer_unknown_business():
    r = client.post("/voice/answer/doesnotexist")
    assert r.status_code == 200
    assert r.json() == []


def test_voice_event_uses_caller_speech(monkeypatch):
    calls = []

    def fake_agent_response(**kwargs):
        history = kwargs["conversation_history"]
        calls.append({"query": kwargs["customer_query"], "history": list(history)})
        history.append({"role": "user", "content": kwargs["customer_query"]})
        history.append({"role": "assistant", "content": "stubbed answer"})
        return "stubbed answer"

    async def fake_tts(text, language):
        return "stubbed.mp3"

    monkeypatch.setattr("main.get_agent_response", fake_agent_response)
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    business_id = _register_business(language="hi")

    r1 = client.post(
        f"/voice/event/{business_id}",
        json={"call_uuid": "call-abc", "speech_results": [{"text": "3 series ka price kya hai?"}]},
    )
    assert r1.status_code == 200, r1.text
    ncco1 = r1.json()
    assert ncco1[0]["action"] == "play"
    assert ncco1[1]["action"] == "input"
    assert calls and calls[0]["query"] == "3 series ka price kya hai?"

    r2 = client.post(
        f"/voice/event/{business_id}",
        json={"call_uuid": "call-abc", "speech_results": [{"text": "aur x5?"}]},
    )
    assert r2.status_code == 200
    assert calls[1]["query"] == "aur x5?"
    assert calls[1]["history"][0]["content"] == "3 series ka price kya hai?"


def test_voice_event_silence_then_farewell(monkeypatch):
    async def fake_tts(text, language):
        return "stubbed.mp3"

    monkeypatch.setattr("main.get_agent_response", lambda **kw: "ok")
    monkeypatch.setattr("main.synthesize_speech", fake_tts)

    business_id = _register_business(language="en")

    r1 = client.post(f"/voice/event/{business_id}", json={"call_uuid": "call-sil", "speech_results": []})
    assert r1.status_code == 200
    ncco1 = r1.json()
    assert ncco1[0]["action"] == "play"
    assert ncco1[1]["action"] == "input"

    r2 = client.post(f"/voice/event/{business_id}", json={"call_uuid": "call-sil", "speech_results": []})
    assert r2.status_code == 200
    ncco2 = r2.json()
    # Farewell is audio in the same human voice — then the call ends
    assert ncco2[0]["action"] == "play"
    assert len(ncco2) == 1


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


def test_voice_event_saves_conversation_and_lead(monkeypatch):
    """Turns are persisted and a lead is extracted when the call ends"""
    import main as main_mod

    def fake_agent_response(**kwargs):
        history = kwargs["conversation_history"]
        history.append({"role": "user", "content": kwargs["customer_query"]})
        history.append({"role": "assistant", "content": "Sure ji, may I have your name and mobile number?"})
        return "Sure ji, may I have your name and mobile number?"

    async def fake_tts(text, language):
        return "stubbed.mp3"

    monkeypatch.setattr("main.get_agent_response", fake_agent_response)
    monkeypatch.setattr("main.synthesize_speech", fake_tts)
    monkeypatch.setattr(
        main_mod,
        "extract_lead_info",
        lambda history: {
            "caller_name": "Raj Patel",
            "caller_number": "9876543210",
            "interest": "BMW X1 test drive",
            "intent": "test_drive",
            "scheduled_for": "tomorrow 5pm",
            "notes": "Wants white colour",
        },
    )

    data = _make_user(name="CRM Owner")
    biz = _make_business(data["token"], name="CRM Motors")
    bid = biz["business_id"]
    call_uuid = f"crm-{uuid.uuid4().hex[:8]}"  # unique per run: DB persists

    client.post(f"/voice/event/{bid}", json={"call_uuid": call_uuid, "speech_results": [{"text": "I want to book a test drive"}]})
    client.post(f"/voice/event/{bid}", json={"call_uuid": call_uuid, "speech_results": [{"text": "My number is 9876543210"}]})
    # two silences → call over → lead extracted
    client.post(f"/voice/event/{bid}", json={"call_uuid": call_uuid, "speech_results": []})
    client.post(f"/voice/event/{bid}", json={"call_uuid": call_uuid, "speech_results": []})

    # Lead appears in the owner's CRM
    leads = client.get("/api/leads", headers=_auth(data["token"])).json()["leads"]
    assert len(leads) == 1
    l = leads[0]
    assert l["caller_name"] == "Raj Patel"
    assert l["caller_number"] == "9876543210"
    assert l["intent"] == "test_drive"
    assert l["scheduled_for"] == "tomorrow 5pm"
    assert l["status"] == "new"

    # Status update works
    r = client.patch(f"/api/leads/{l['id']}", json={"status": "contacted"}, headers=_auth(data["token"]))
    assert r.status_code == 200 and r.json()["status"] == "contacted"

    # Transcript view shows the exchanges
    calls = client.get("/api/calls", headers=_auth(data["token"])).json()["calls"]
    assert calls and calls[0]["turns"], "transcript turns were not saved"
    assert any("test drive" in t["caller"] for t in calls[0]["turns"])


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


def test_call_now_without_vonage_honest_failure():
    """Without Vonage keys, the API says so in plain language (and env may have keys — accept both)"""
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
    def failing_place_call(to_number, answer_url, from_number=None):
        return {"ok": False, "call_uuid": None, "detail": "Vonage account has no calling credit left — top it up in the Vonage dashboard."}

    monkeypatch.setattr("main.place_call", failing_place_call)

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


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
