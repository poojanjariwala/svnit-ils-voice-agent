# Queries Vonage for the most recent call and prints its status.
import os, sys, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_service
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), override=True)

req = urllib.request.Request("https://api.nexmo.com/v1/calls?page_size=5&order=desc",
                             headers={"Authorization": "Bearer " + phone_service._jwt()})
try:
    d = json.load(urllib.request.urlopen(req, timeout=20))
    for c in d.get("_embedded", {}).get("calls", []):
        print(c.get("status"), "|", c.get("direction"), "to", c.get("to"), "| from", c.get("from"),
              "|", c.get("start_time"), "|", (c.get("conversation_uuid") or "")[:8])
except Exception as e:
    body = getattr(e, "read", lambda: b"")()
    print("ERROR:", e, body.decode()[:300] if body else "")
