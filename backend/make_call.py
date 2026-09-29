# Places a REAL outbound call to a mobile number.
#
# Because this environment reaps detached background processes,
# this script keeps the public tunnel alive for the whole call:
#   1. start localtunnel (child process, stays alive while we run)
#   2. point .env + Vonage webhooks at the tunnel URL
#   3. restart uvicorn so it picks up the new PUBLIC_BASE_URL
#   4. place the call via the app's own API
#   5. keep the tunnel alive until the call window ends, then clean up
#
# Usage:  python make_call.py <mobile_number> [business_id]
import os
import re
import sys
import json
import time
import base64
import subprocess
import urllib.request

BACKEND = os.path.dirname(os.path.abspath(__file__))
DETACHED = 0x00000008 | 0x00000200
LT_JS = r"C:\Users\acer\AppData\Roaming\npm\node_modules\localtunnel\bin\lt.js"

TO_NUMBER = sys.argv[1] if len(sys.argv) > 1 else ""
BUSINESS_ID = sys.argv[2] if len(sys.argv) > 2 else "7e40a853"
CALL_WINDOW_SECONDS = 420  # keep tunnel up 7 minutes

assert TO_NUMBER, "usage: python make_call.py <mobile> [business_id]"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


# ── 1. tunnel ────────────────────────────────────────────────
tunnel_log = open(os.path.join(BACKEND, "tunnel.log"), "w")
tunnel = subprocess.Popen(
    ["node", LT_JS, "--port", "8000"],
    stdout=tunnel_log, stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL, creationflags=DETACHED,
)
print(f"[1/5] tunnel starting (pid {tunnel.pid})…")

base_url = None
for _ in range(30):
    time.sleep(1)
    try:
        m = re.search(r"(https://[a-z0-9\-]+\.loca\.lt)", open(os.path.join(BACKEND, "tunnel.log")).read())
        if m:
            base_url = m.group(1)
            break
    except OSError:
        pass
assert base_url, "tunnel failed to start"
print(f"[1/5] tunnel UP: {base_url}")

# ── 2. point .env + Vonage at it ─────────────────────────────
env_path = os.path.join(BACKEND, "..", ".env")
env_src = open(env_path, encoding="utf-8").read()
env_src = re.sub(r"(?m)^PUBLIC_BASE_URL=.*$", f"PUBLIC_BASE_URL={base_url}", env_src)
open(env_path, "w", encoding="utf-8").write(env_src)

# reload env values
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(BACKEND, "..", ".env"), override=True)
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
import base64 as b64  # noqa: E402
req.add_header("Authorization", "Basic " + b64.b64encode(f"{VK}:{VS}".encode()).decode())
resp = urllib.request.urlopen(req, timeout=30)
print(f"[2/5] Vonage webhooks -> tunnel ({resp.status})")

# ── 3. restart uvicorn with the new PUBLIC_BASE_URL ──────────
out = sh("taskkill /F /IM python.exe /FI \"WINDOWTITLE ne VoxAgentKeep\" 2>nul | findstr uvicorn")
# simpler: kill anything listening on 8000, then start fresh
out = sh("for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do @taskkill /F /PID %a 2>nul")
time.sleep(1)
uv_log = open(os.path.join(BACKEND, "server.log"), "w")
uvicorn = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", "--port", "8000"],
    cwd=BACKEND, stdout=uv_log, stderr=subprocess.STDOUT,
    creationflags=DETACHED,
)
print(f"[3/5] uvicorn restarted (pid {uvicorn.pid})")
time.sleep(5)

# verify through the tunnel
import requests  # noqa: E402
r = requests.get(f"{base_url}/api/health", timeout=20)
print(f"[3/5] tunnel->app check: {r.status_code}")
assert r.status_code == 200, "app not reachable through tunnel"

# ── 4. place the call ────────────────────────────────────────
sys.path.insert(0, BACKEND)
import phone_service  # noqa: E402

result = phone_service.place_call(
    to_number=TO_NUMBER,
    answer_url=f"{base_url}/voice/answer/{BUSINESS_ID}",
)
print(f"[4/5] CALL RESULT: {json.dumps(result, indent=2)}")

if result["ok"]:
    print(f"\n📞 RINGING {TO_NUMBER} — answer your phone, Meera is calling!")
    print("     (Say things like 'what is the price of the 3 series?')")
else:
    print("\nCall failed — see detail above.")

# ── 5. keep the tunnel alive for the call window ─────────────
print(f"[5/5] keeping the line open for {CALL_WINDOW_SECONDS // 60} minutes…")
try:
    for i in range(CALL_WINDOW_SECONDS, 0, -30):
        # self-heal the tunnel if it drops mid-call
        try:
            if requests.get(f"{base_url}/api/health", timeout=10).status_code != 200:
                raise RuntimeError()
        except Exception:
            print("…tunnel dropped, restarting it")
            tunnel.kill()
            tunnel_log = open(os.path.join(BACKEND, "tunnel.log"), "w")
            tunnel = subprocess.Popen(
                ["node", LT_JS, "--port", "8000"],
                stdout=tunnel_log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=DETACHED,
            )
        time.sleep(min(30, i))
finally:
    tunnel.kill()
    print("done — tunnel closed.")
