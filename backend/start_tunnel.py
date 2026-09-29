# Starts the localtunnel detached (survives shell exit), waits for the URL,
# and prints it. Windows-safe.
import subprocess
import time
import re

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
LT_JS = r"C:\Users\acer\AppData\Roaming\npm\node_modules\localtunnel\bin\lt.js"

log = open("tunnel.log", "w")
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
        m = re.search(r"(https://[a-z0-9\-]+\.loca\.lt)", open("tunnel.log").read())
        if m:
            url = m.group(1)
            break
    except OSError:
        pass

print("URL:", url)
if url:
    import requests
    try:
        r = requests.get(f"{url}/api/health", timeout=20)
        print("tunnel health:", r.status_code)
    except Exception as e:
        print("tunnel check failed:", e)
