# Live smoke test against a running server on :8000.
# Exercises the real HTTP surface: health, login, agent list, dashboard,
# and the Vonage answer webhook (which builds NCCO with the public URL).
import requests, sys

BASE = "http://localhost:8000"
ok = True

def check(name, cond, detail=""):
    global ok
    print(("PASS" if cond else "FAIL"), "-", name, detail)
    ok = ok and cond

# 1. health
r = requests.get(f"{BASE}/api/health", timeout=10)
check("health 200", r.status_code == 200, r.text[:80])

# 2. login with the seeded test business
r = requests.post(f"{BASE}/api/auth/login", json={"email": "dialcheck@motors.in", "password": "dialpass123"}, timeout=15)
check("login 200", r.status_code == 200, r.text[:80])
token = r.json().get("token", "") if r.status_code == 200 else ""
H = {"Authorization": f"Bearer {token}"}

# 3. list my businesses (agent list for the owner)
r = requests.get(f"{BASE}/api/businesses", headers=H, timeout=15)
agents = r.json() if r.status_code == 200 else []
biz_list = agents.get("businesses", []) if isinstance(agents, dict) else agents
check("businesses list has Dial Motors", any(b.get("name") == "Dial Motors" for b in biz_list), f"{len(biz_list)} businesses")

# 4. dashboard serves
r = requests.get(f"{BASE}/", timeout=15)
check("dashboard HTML served", r.status_code == 200 and "html" in r.text[:200].lower(), f"{len(r.text)} bytes")

# 5. Vonage answer webhook returns a valid NCCO using the tunnel URL
r = requests.post(f"{BASE}/voice/answer/7e40a853", timeout=60)
ncco_ok = r.status_code == 200
try:
    ncco = r.json()
    ncco_ok = ncco_ok and isinstance(ncco, list) and len(ncco) > 0 and ncco[0].get("action") in ("talk", "play")
    blob = r.text
    check("NCCO uses public tunnel URL (not localhost)", "loca.lt" in blob and "localhost" not in blob, blob[:120])
except Exception as e:
    ncco_ok = False
check("NCCO answer 200 + valid actions", ncco_ok, r.text[:120])

# 6. CRM endpoints respond for the owner
r = requests.get(f"{BASE}/api/leads", headers=H, timeout=15)
check("leads endpoint 200", r.status_code == 200, r.text[:80])

sys.exit(0 if ok else 1)
