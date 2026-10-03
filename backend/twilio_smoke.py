# Live smoke: Twilio TwiML twins on the running server.
import requests, sys

BASE = "http://localhost:8000"
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
r = requests.post(f"{BASE}/twilio/answer/{bid}", timeout=60)
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
r = requests.post(f"{BASE}/twilio/event/{bid}", data={"CallSid": "CA-smoke-1", "SpeechResult": "What are your timings?"}, timeout=60)
check("twilio/event 200 with reply", r.status_code == 200 and "<Play>" in r.text and "<Gather" in r.text, r.status_code)

# 3. silence → reprompt then hangup
r1 = requests.post(f"{BASE}/twilio/event/{bid}", data={"CallSid": "CA-smoke-1", "SpeechResult": ""}, timeout=60)
r2 = requests.post(f"{BASE}/twilio/event/{bid}", data={"CallSid": "CA-smoke-1", "SpeechResult": ""}, timeout=60)
check("silence 1 reprompts", "<Gather" in r1.text, "")
check("silence 2 hangs up", "<Hangup/>" in r2.text, "")

# 4. phone status shows twilio/disconnected honestly
r = requests.get(f"{BASE}/api/phone/status", headers=H, timeout=15)
d = r.json()
check("phone/status honest", r.status_code == 200 and "connected" in d and "provider" in d, str(d))

sys.exit(0 if ok else 1)
