# Twilio outgoing-caller-ID verification for the user's mobile.
#   python twilio_verify.py start       -> Twilio CALLS the mobile, speaks a 6-digit code
#   python twilio_verify.py code 123456 -> completes verification with that code
import os, sys, json, base64, urllib.request, urllib.parse

BACKEND = os.path.dirname(os.path.abspath(__file__))
sid = tok = None
for line in open(os.path.join(BACKEND, "..", ".env"), encoding="utf-8"):
    line = line.strip()
    if line.startswith("TWILIO_ACCOUNT_SID="):
        sid = line.split("=", 1)[1].strip()
    if line.startswith("TWILIO_AUTH_TOKEN="):
        tok = line.split("=", 1)[1].strip()
assert sid and tok, "Twilio creds missing from .env"

MOBILE = "+918200344429"

def tw(method, path, params=None):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{tok}".encode()).decode())
    data = None
    if method == "POST":
        data = urllib.parse.urlencode(params or {}).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        resp = urllib.request.urlopen(req, data, timeout=30)
        return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"__error__": e.code, "__body__": e.read().decode()[:300]}

if len(sys.argv) > 1 and sys.argv[1] == "start":
    res = tw("POST", "/OutgoingCallerIds.json", {"PhoneNumber": MOBILE, "FriendlyName": "Poojan Mobile"})
    if "__error__" in res:
        print("START FAILED:", res)
    else:
        print("CALLING", MOBILE, "- answer and note the 6-digit code")
        print("verification sid:", res.get("sid"))
elif len(sys.argv) > 2 and sys.argv[1] == "code":
    code = sys.argv[2].strip()
    # find the pending verification
    page = tw("GET", "/OutgoingCallerIds.json", {"PhoneNumber": MOBILE})
    items = page.get("outgoing_caller_ids", [])
    if "__error__" in page or not items:
        print("no pending verification found:", page)
        sys.exit(1)
    vsid = items[0]["sid"]
    res = tw("PUT", f"/OutgoingCallerIds/{vsid}.json", {"VerificationCode": code})
    if "__error__" in res:
        print("CODE REJECTED:", res)
    else:
        print("VERIFIED:", res.get("phone_number"), "is now callable from the trial account")
else:
    print("usage: python twilio_verify.py start | python twilio_verify.py code <6digits>")
