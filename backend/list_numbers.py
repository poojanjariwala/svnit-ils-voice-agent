# Lists numbers owned by the Vonage account + balance.
import os, sys, json, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
BACKEND = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BACKEND, "..", ".env"), override=True)
VK = os.getenv("VONAGE_API_KEY", "")
VS = os.getenv("VONAGE_API_SECRET", "")

q = urllib.parse.urlencode({"api_key": VK, "api_secret": VS})
for name, url in [
    ("OWNED NUMBERS", f"https://rest.nexmo.com/account/numbers?{q}"),
    ("BALANCE", f"https://rest.nexmo.com/account/get-balance?{q}"),
]:
    try:
        d = json.load(urllib.request.urlopen(url, timeout=20))
        print(f"{name}: {json.dumps(d, indent=2)[:800]}")
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        print(f"{name} ERROR:", e, body.decode()[:300])
