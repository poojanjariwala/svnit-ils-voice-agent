# Live smoke: Twilio TwiML twins on the running server.
import requests, sys

BASE = "http://localhost:8000"

# Every /twilio/* webhook now demands a valid X-Twilio-Signature, so the
# smoke script has to sign exactly the way Twilio does.
import hashlib, hmac, base64, os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

def sign(url, data):
    if not AUTH_TOKEN:
        return {}
    payload = url
    for k in sorted(set(data or {})):
        payload += f"{k}{data[k]}"
    sig = base64.b64encode(
        hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode().strip()
    return {"X-Twilio-Signature": sig}

def tw_post(path, data=None):
    url = f"{BASE}{path}"
    return requests.post(url, data=data, headers=sign(url, data), timeout=60)

ok = True

def check(name, cond, detail=""):
    global ok
    print(("PASS" if cond else "FAIL"), "-", name, detail)
    ok = ok and cond

# login to get a real business
r = requests.post(f"{BASE}/api/auth/login", json={"email": "dialcheck@motors.in", "password": "dialpass123"}, timeout=15)
token = r.json().get("token", "")
H = {"Authorization": f"Bearer {token}"}
bid = "7e40a853"

# 1. TwiML answer
r = tw_post(f"/twilio/answer/{bid}")
xml = r.text
check("twilio/answer 200 XML", r.status_code == 200 and "<Response>" in xml, r.status_code)
check("TwiML Play + Gather", "<Play>" in xml and '<Gather input="speech"' in xml, xml[:90])
check("audio served over tunnel URL", "loca.lt/audio/" in xml, "")

# the Play URL actually serves audio
import re
m = re.search(r"<Play>(.*?)</Play>", xml)
if m:
    ar = requests.get(m.group(1), timeout=30)
    check("greeting audio downloadable", ar.status_code == 200 and ar.headers.get("content-type", "").startswith("audio"), f"{ar.status_code} {ar.headers.get('content-type','')[:24]}")
else:
    check("greeting audio downloadable", False, "no Play url")

# 2. TwiML event with speech
r = tw_post(f"/twilio/event/{bid}", {"CallSid": "CA-smoke-1", "SpeechResult": "What are your timings?"})
check("twilio/event 200 with reply", r.status_code == 200 and "<Play>" in r.text and "<Gather" in r.text, r.status_code)

# 3. silence → reprompt then hangup
r1 = tw_post(f"/twilio/event/{bid}", {"CallSid": "CA-smoke-1", "SpeechResult": ""})
r2 = tw_post(f"/twilio/event/{bid}", {"CallSid": "CA-smoke-1", "SpeechResult": ""})
check("silence 1 reprompts", "<Gather" in r1.text, "")
check("silence 2 hangs up", "<Hangup/>" in r2.text, "")

# 4. phone status shows twilio/disconnected honestly
r = requests.get(f"{BASE}/api/phone/status", headers=H, timeout=15)
d = r.json()
check("phone/status honest", r.status_code == 200 and "connected" in d and "provider" in d, str(d))


# 5. an unsigned/forged webhook must be refused — this is VULN-003
r = requests.post(f"{BASE}/twilio/event/{bid}", data={"CallSid": "CA-forged", "SpeechResult": "buy"}, timeout=30)
check("forged webhook rejected", r.status_code == 403, str(r.status_code))

sys.exit(0 if ok else 1)
