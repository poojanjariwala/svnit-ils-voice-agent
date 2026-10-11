# Prints full detail of recent Vonage calls.
import os, sys, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_service
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), override=True)

req = urllib.request.Request("https://api.nexmo.com/v1/calls/e8597beb-3e32-46ed-8d0e-c9998cb30d70",
                             headers={"Authorization": "Bearer " + phone_service._jwt()})
try:
    c = json.load(urllib.request.urlopen(req, timeout=20))
except Exception as e:
    body = getattr(e, "read", lambda: b"")()
    c = {"error": str(e), "body": body.decode()[:400]}
print(json.dumps(c, indent=2))
