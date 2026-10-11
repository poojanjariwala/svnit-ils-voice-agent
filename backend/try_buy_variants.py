# Retries number/buy with alternative request shapes in case the earlier
# 401 was request-shape related rather than an account restriction.
import os, sys, json, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
BACKEND = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BACKEND, "..", ".env"), override=True)
VK = os.getenv("VONAGE_API_KEY", "")
VS = os.getenv("VONAGE_API_SECRET", "")
MSISDN = sys.argv[1] if len(sys.argv) > 1 else "12017358528"

def post(url, data_bytes, headers=None):
    req = urllib.request.Request(url, data=data_bytes, method="POST")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=25)
        return resp.status, resp.read().decode()[:300]
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        return getattr(e, "code", 0), body.decode()[:300]

body = urllib.parse.urlencode({"country": "US", "msisdn": MSISDN}).encode()
variants = [
    ("creds-in-query + form body",
     f"https://rest.nexmo.com/number/buy?api_key={VK}&api_secret={VS}", body, {}),
    ("creds-in-body (repeat test)",
     "https://rest.nexmo.com/number/buy",
     urllib.parse.urlencode({"api_key": VK, "api_secret": VS, "country": "US", "msisdn": MSISDN}).encode(), {}),
]
for name, url, data, hdrs in variants:
    st, txt = post(url, data, hdrs)
    print(f"[{name}] HTTP {st}: {txt}")
    if st in (200, 201, 204):
        print("PURCHASED")
        sys.exit(0)
print("ALL VARIANTS FAILED — account-level restriction confirmed")
