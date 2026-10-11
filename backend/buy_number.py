# Searches for a cheap voice-capable US number and buys it via the API.
# If the API refuses (trial restriction), prints the exact error so we
# know to buy it in the dashboard instead.
import os, sys, json, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
BACKEND = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BACKEND, "..", ".env"), override=True)
VK = os.getenv("VONAGE_API_KEY", "")
VS = os.getenv("VONAGE_API_SECRET", "")

def rest(path, params=None, form=None):
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(f"https://rest.nexmo.com{path}", data=data, method="POST")
    else:
        q = urllib.parse.urlencode(params or {})
        req = urllib.request.Request(f"https://rest.nexmo.com{path}?{q}")
    try:
        return json.load(urllib.request.urlopen(req, timeout=25))
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        return {"__error__": str(e), "__body__": body.decode()[:300]}

# 1. search
res = rest("/number/search", {"api_key": VK, "api_secret": VS, "country": "US", "features": "VOICE", "size": 5})
if "__error__" in res:
    print("SEARCH ERROR:", res)
    sys.exit(1)
numbers = res.get("numbers", [])
print(f"found {len(numbers)} candidates")
if not numbers:
    sys.exit("no numbers found")

msisdn = numbers[0]["msisdn"]
print("buying:", msisdn, numbers[0].get("features"), "cost:", numbers[0].get("monthly_cost", "?"))

# 2. buy
res2 = rest("/number/buy", form={"api_key": VK, "api_secret": VS, "country": "US", "msisdn": msisdn})
print("BUY RESULT:", json.dumps(res2, indent=2)[:500])
