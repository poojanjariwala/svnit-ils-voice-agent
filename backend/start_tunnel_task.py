# backend/start_tunnel_task.py
# ─────────────────────────────────────────────
# Owner  : Buffy
# Task   : Start the public tunnel AND tell the app which URL it really got.
#
# Why: localtunnel does not always get the subdomain you ask for. It was
# handed "curly-fish-50" while public_url.txt still said
# "public-things-appear", so the app kept sending Twilio webhook URLs to a
# dead host and every call 503'd. Starting the tunnel and syncing the real
# URL have to be one step.
#
# Run:  python start_tunnel_task.py      (then it stays in the foreground)
# ─────────────────────────────────────────────
import os
import re
import subprocess
import sys
import time

BACKEND = os.path.dirname(os.path.abspath(__file__))
# node writes here; our own stdout is redirected elsewhere by the .bat,
# and on Windows one file cannot be opened twice for writing
LOG = os.path.join(BACKEND, "tunnel_node.log")
PUBLIC_FILE = os.path.join(BACKEND, "public_url.txt")
LT_JS = r"C:\Users\acer\AppData\Roaming\npm\node_modules\localtunnel\bin\lt.js"
SUBDOMAIN = "public-things-appear"   # best-effort; not guaranteed


def main() -> int:
    with open(LOG, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            ["node", LT_JS, "--port", "8000", "--subdomain", SUBDOMAIN],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )

    url = None
    for _ in range(40):                       # ~40s to get a URL from localtunnel
        time.sleep(1)
        try:
            with open(LOG, encoding="utf-8", errors="ignore") as fh:
                m = re.search(r"https://[a-z0-9\-]+\.loca\.lt", fh.read())
            if m:
                url = m.group(0)
                break
        except OSError:
            pass

    if not url:
        print("tunnel did not report a URL — check tunnel.log", file=sys.stderr)
        return 1

    with open(PUBLIC_FILE, "w", encoding="utf-8") as fh:
        fh.write(url)
    print(f"tunnel up at {url} (app now using it)")

    # keep the tunnel in the foreground so the scheduled task tracks its life
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())