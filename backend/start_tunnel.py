# Starts the localtunnel detached (survives shell exit), waits for the URL,
# writes it to public_url.txt (main.py reads that file at request time, so the
# app picks up a new URL without a restart), then health-checks it.
# Windows-safe. Run from anywhere:  python backend/start_tunnel.py
import os
import subprocess
import time
import re

BACKEND = os.path.dirname(os.path.abspath(__file__))
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
LT_JS = r"C:\Users\acer\AppData\Roaming\npm\node_modules\localtunnel\bin\lt.js"

log = open(os.path.join(BACKEND, "tunnel.log"), "w")
p = subprocess.Popen(
    ["node", LT_JS, "--port", "8000"],
    stdout=log, stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL, creationflags=DETACHED,
)
print("tunnel pid", p.pid)

url = None
for _ in range(25):
    time.sleep(1)
    try:
        m = re.search(r"(https://[a-z0-9\-]+\.loca\.lt)", open(os.path.join(BACKEND, "tunnel.log")).read())
        if m:
            url = m.group(1)
            break
    except OSError:
        pass

print("URL:", url)
if url:
    with open(os.path.join(BACKEND, "public_url.txt"), "w") as fh:
        fh.write(url)
    print(f"wrote {os.path.join(BACKEND, 'public_url.txt')} — the app now uses {url}")
    import requests
    try:
        r = requests.get(f"{url}/api/health", timeout=20)
        print("tunnel health:", r.status_code)
    except Exception as e:
        print("tunnel check failed:", e)
