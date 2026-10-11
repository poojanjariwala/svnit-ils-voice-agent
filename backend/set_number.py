# Sets VONAGE_PHONE_NUMBER in .env to the number you bought.
# Usage:  python set_number.py 12017358528
import os, sys, re
BACKEND = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BACKEND, "..", ".env")

assert len(sys.argv) > 1, "usage: python set_number.py <number-you-bought>"
number = re.sub(r"\D", "", sys.argv[1])
assert 10 <= len(number) <= 15, "number must be 10-15 digits"

with open(ENV_PATH, "r", encoding="utf-8") as f:
    content = f.read()

line = f"VONAGE_PHONE_NUMBER={number}"
if re.search(r"^VONAGE_PHONE_NUMBER=.*$", content, flags=re.M):
    content = re.sub(r"^VONAGE_PHONE_NUMBER=.*$", line, content, flags=re.M)
else:
    content = content.rstrip("\n") + "\n" + line + "\n"

with open(ENV_PATH, "w", encoding="utf-8") as f:
    f.write(content)
print(f"OK — VONAGE_PHONE_NUMBER set to {number}")
print(f"Now run:  the scheduled task (start_task.ps1) or make_call.py to place the call")
