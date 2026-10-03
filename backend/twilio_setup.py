# Validates Twilio credentials, claims the free trial number if needed,
# and writes TWILIO_* into .env.
import os, sys, json, urllib.request, urllib.parse

BACKEND = os.path.dirname(os.path.abspath(__file__))
sid = tok = None
# 1. pull creds from .env.example (user pasted them there)
for line in open(os.path.join(BACKEND, "..", ".env.example"), encoding="utf-8"):
    line = line.strip()
    if line.startswith("TWILIO_ACCOUNT_SID=") and "xxx" not in line:
        sid = line.split("=", 1)[1].strip()
    if line.startswith("TWILIO_AUTH_TOKEN=") and "your_" not in line:
        tok = line.split("=", 1)[1].strip()
assert sid and tok, "no real Twilio credentials found in .env.example"
print(f"creds: SID {sid[:8]}...{sid[-4:]}, token loaded ({len(tok)} chars)")

def tw(method, path, params=None):
    """Twilio REST helper with Basic auth. Returns parsed JSON."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}{path}"
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{tok}".encode()).decode())
    if method == "POST":
        data = urllib.parse.urlencode(params or {}).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    else:
        data = None
    try:
        resp = urllib.request.urlopen(req, data, timeout=30)
        return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"__error__": e.code, "__body__": body}

# 2. validate credentials
acct = tw("GET", ".json")
if "__error__" in acct:
    print("AUTH FAILED:", acct); sys.exit(1)
print(f"account OK: '{acct.get('friendly_name')}' status={acct.get('status')} type={acct.get('type')}")

# 3. existing numbers?
page = tw("GET", "/IncomingPhoneNumbers.json")
nums = [n["phone_number"] for n in page.get("incoming_phone_numbers", [])]
print("existing numbers:", nums or "none")

if not nums:
    # 4. claim a free trial voice number (US, cheap, first one is free on trial)
    search = tw("GET", "/AvailablePhoneNumbers/US/Local.json", {"VoiceEnabled": "true", "PageSize": "5"})
    cands = search.get("available_phone_numbers", [])
    if not cands:
        print("no candidates"); sys.exit(1)
    pick = cands[0]["phone_number"]
    print("claiming:", pick)
    buy = tw("POST", "/IncomingPhoneNumbers.json", {"PhoneNumber": pick})
    if "__error__" in buy:
        print("CLAIM FAILED:", buy)
        print("-> claim it in the console instead: Phone Numbers > Buy a number (free on trial)")
        sys.exit(1)
    nums = [buy.get("phone_number") or pick]
    print("claimed OK")

number = nums[0]
print("USING NUMBER:", number)

# 5. write to .env
env_path = os.path.join(BACKEND, "..", ".env")
lines = open(env_path, encoding="utf-8").read().splitlines()
out = [l for l in lines if not l.startswith(("TWILIO_ACCOUNT_SID=", "TWILIO_AUTH_TOKEN=", "TWILIO_PHONE_NUMBER="))]
out += ["", "# ── TWILIO (active calling provider) ─────────", f"TWILIO_ACCOUNT_SID={sid}", f"TWILIO_AUTH_TOKEN={tok}", f"TWILIO_PHONE_NUMBER={number}"]
open(env_path, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(".env updated (creds + number, placeholders removed)")
