# Places a REAL outbound call to a mobile number.
#
# Keeps the public tunnel alive for the whole call:
#   1. kill stale tunnels, start localtunnel (child, alive while we run)
#   2. write the tunnel URL to backend/public_url.txt — the running app
#      picks it up instantly (no server restart needed)
#   3. point Vonage webhooks at the tunnel
#   4. place the call via the Vonage API
#   5. hold the tunnel open (self-healing) until the call window ends
#
# Usage:  python make_call.py <mobile_number> [business_id]
import os
import re
import sys
import json
import time
import subprocess
import urllib.request

BACKEND = os.path.dirname(os.path.abspath(__file__))
DETACHED = 0x00000008 | 0x00000200
LT_JS = r"C:\Users\acer\AppData\Roaming\npm\node_modules\localtunnel\bin\lt.js"

TO_NUMBER = sys.argv[1] if len(sys.argv) > 1 else ""
BUSINESS_ID = sys.argv[2] if len(sys.argv) > 2 else "7e40a853"
CALL_WINDOW_SECONDS = 900  # keep tunnel up 15 minutes

assert TO_NUMBER, "usage: python make_call.py <mobile> [business_id]"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def start_tunnel():
    log = open(os.path.join(BACKEND, "tunnel.log"), "w")
    p = subprocess.Popen(
        ["node", LT_JS, "--port", "8000"],
        stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=DETACHED,
    )
    url = None
    for _ in range(30):
        time.sleep(1)
        try:
            m = re.search(r"(https://[a-z0-9\-]+\.loca\.lt)", open(os.path.join(BACKEND, "tunnel.log")).read())
            if m:
                url = m.group(1)
                break
        except OSError:
            pass
    return p, url


def tunnel_ok(url):
    try:
        import requests
        # verify it reaches OUR app, not just the tunnel edge
        return requests.get(f"{url}/api/health", timeout=15).status_code == 200
    except Exception:
        return False


# ── 0. make sure the app server is running ───────────────────
import requests  # noqa: E402


def app_ok():
    try:
        return requests.get("http://localhost:8000/api/health", timeout=5).status_code == 200
    except Exception:
        return False


server = None
if not app_ok():
    slog = open(os.path.join(BACKEND, "server.log"), "w")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=BACKEND, stdout=slog, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=DETACHED,
    )
    for _ in range(30):
        time.sleep(1)
        if app_ok():
            break
assert app_ok(), "uvicorn failed to start (see backend/server.log)"
print("[0/5] app server UP on :8000")

# ── 1. fresh tunnel ──────────────────────────────────────────
sh("taskkill /F /IM node.exe 2>nul")
time.sleep(1)
tunnel, base_url = start_tunnel()
print(f"[1/5] tunnel UP: {base_url}")
assert base_url, "tunnel failed to start"

# wait until the tunnel actually reaches the running app
ok = False
for attempt in range(15):
    if tunnel_ok(base_url):
        ok = True
        break
    print(f"     tunnel warming up… ({attempt + 1})")
    time.sleep(2)
assert ok, "tunnel is not reaching the app"

# ── 2. tell the running app its public URL (no restart!) ─────
with open(os.path.join(BACKEND, "public_url.txt"), "w", encoding="utf-8") as f:
    f.write(base_url)
print(f"[2/5] app public URL -> {base_url} (picked up live)")

# ── 3. provider setup ────────────────────────────────────────
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(BACKEND, "..", ".env"), override=True)
sys.path.insert(0, BACKEND)
import phone_service  # noqa: E402
import base64 as b64  # noqa: E402
import urllib.parse as up  # noqa: E402

PROVIDER = phone_service.active_provider()
print(f"[*] provider: {PROVIDER}")

if PROVIDER == "vonage":
    VK = os.getenv("VONAGE_API_KEY", "")
    VS = os.getenv("VONAGE_API_SECRET", "")
    APP_ID = os.getenv("VONAGE_APPLICATION_ID", "")
    req = urllib.request.Request(
        f"https://api.nexmo.com/v2/applications/{APP_ID}",
        data=json.dumps({
            "name": "VoxAgent Voice Agent",
            "capabilities": {"voice": {"webhooks": {
                "answer_url": {"address": f"{base_url}/voice/answer/APPID", "http_method": "POST"},
                "event_url": {"address": f"{base_url}/voice/event/APPID", "http_method": "POST"},
            }}},
        }).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    req.add_header("Authorization", "Basic " + b64.b64encode(f"{VK}:{VS}".encode()).decode())
    resp = urllib.request.urlopen(req, timeout=30)
    print(f"[3/5] Vonage webhooks -> tunnel ({resp.status})")
else:
    print("[3/5] Twilio needs no webhook registration — answer URL passed per call")

# ── 4. place the call ────────────────────────────────────────
answer_prefix = "twilio" if PROVIDER == "twilio" else "voice"
result = phone_service.place_call(
    to_number=TO_NUMBER,
    answer_url=f"{base_url}/{answer_prefix}/answer/{BUSINESS_ID}",
    status_callback=f"{base_url}/twilio/status/{BUSINESS_ID}",
)
print(f"[4/5] CALL RESULT: {json.dumps(result, indent=2)}")

if result["ok"]:
    print(f"\n📞 RINGING {TO_NUMBER} — answer your phone, Meera is calling!")
    # watch the call's real status (Vonage: GET /v1/calls, Twilio: GET Calls.json)
    cuuid = result.get("call_uuid", "")
    last = ""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    tok = os.getenv("TWILIO_AUTH_TOKEN", "")
    for _ in range(12):
        time.sleep(5)
        try:
            if PROVIDER == "twilio":
                q = up.urlencode({"Sid": cuuid})
                req2 = urllib.request.Request(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json?{q}")
                req2.add_header("Authorization", "Basic " + b64.b64encode(f"{sid}:{tok}".encode()).decode())
                calls = json.load(urllib.request.urlopen(req2, timeout=15)).get("calls", [])
                st = calls[0].get("status", "?") if calls else "?"
            else:
                req2 = urllib.request.Request(f"https://api.nexmo.com/v1/calls/{cuuid}",
                                              headers={"Authorization": "Bearer " + phone_service._jwt()})
                st = json.load(urllib.request.urlopen(req2, timeout=15)).get("status", "?")
            if st != last:
                print(f"     call status: {st}")
                last = st
            if st in ("completed", "failed", "rejected", "busy", "cancelled", "timeout", "no-answer", "canceled"):
                break
        except Exception:
            pass
else:
    print("\nCall failed — see the detail above.")

# ── 5. keep the tunnel alive for the call window ─────────────
print(f"[5/5] keeping the line open for {CALL_WINDOW_SECONDS // 60} minutes… (do not close this terminal)")
try:
    end = time.time() + CALL_WINDOW_SECONDS
    while time.time() < end:
        time.sleep(20)
        if not tunnel_ok(base_url):
            print("…tunnel dropped, restarting it")
            try:
                tunnel.kill()
            except Exception:
                pass
            sh("taskkill /F /IM node.exe 2>nul")
            time.sleep(1)
            tunnel, new_url = start_tunnel()
            if new_url and new_url != base_url:
                base_url = new_url
                with open(os.path.join(BACKEND, "public_url.txt"), "w", encoding="utf-8") as f:
                    f.write(base_url)
                print(f"     new tunnel: {base_url} (picked up live again)")
finally:
    try:
        tunnel.kill()
    except Exception:
        pass
    print("done — tunnel closed.")
