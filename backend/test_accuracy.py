# backend/test_accuracy.py
# Ad-hoc accuracy battery: 10 KB-grounded questions (en/hi/gu) + STT round-trip.
# Run from backend/:  PYTHONIOENCODING=utf-8 python test_accuracy.py
import asyncio
import os
import sqlite3
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.insert(0, os.path.dirname(__file__))

import logging  # noqa: E402

logging.disable(logging.INFO)

from llm_service import get_agent_response  # noqa: E402
from voice_service import synthesize_speech, transcribe_audio  # noqa: E402

# ── Load the real knowledge base for the registered kirana business ──
DB_PATH = os.path.join(os.path.dirname(__file__), "business_voice_agents.db")
conn = sqlite3.connect(DB_PATH)
row = conn.execute(
    "SELECT id, name, knowledge_base, language FROM businesses WHERE id='67a8c7e1'"
).fetchone()
conn.close()
if not row:
    print("FATAL: business 67a8c7e1 not found — register it first")
    sys.exit(1)

BID, BNAME, KB, BLANG = row
print(f"Business: {BNAME} ({BID}) | KB chars: {len(KB)} | lang: {BLANG}\n")

# ── 10 grounded questions: (lang, question, [any-of accepted substrings]) ──
CASES = [
    ("en", "What is the price of toor dal?",               ["45"]),
    ("en", "What are your store hours?",                   ["9", "9AM"]),
    ("en", "Do you accept UPI payments?",                  ["UPI", "yes", "Yes"]),
    ("en", "What is the bulk discount?",                   ["2000", "5"]),
    ("en", "What is the price of mustard oil?",            ["140"]),
    ("hi", "चीनी का दाम क्या है?",                          ["42"]),
    ("hi", "रविवार को कितने बजे तक खुला रहता है?",          ["1"]),
    ("gu", "basmati rice no bhav su che?",                 ["80"]),
    ("gu", "ઘરે ડિલિવરી મળે છે?",                            ["500", "હા"]),
    ("gu", "chana dal nu bhav su che?",                    ["50"]),
]

passed = 0
for i, (lang, q, accept) in enumerate(CASES, 1):
    ans = get_agent_response(
        business_name=BNAME,
        knowledge_base=KB,
        language_code=lang,
        customer_query=q,
        conversation_history=[],
    )
    norm = (ans or "").lower()
    ok = any(a.lower() in norm for a in accept)
    passed += ok
    print(f"[{i:2d}] {lang} | {'PASS' if ok else 'FAIL'} | Q: {q}")
    print(f"     A: {(ans or '').strip()[:160]}")

print(f"\n=== ACCURACY: {passed}/{len(CASES)} ===")

# ── STT round-trip: Edge TTS (en) → Groq Whisper transcription ──
print("\n=== STT ROUND-TRIP ===")
try:
    audio_file = asyncio.run(
        synthesize_speech("What are your store hours today?", "en")
    )
    path = os.path.join("audio_files", audio_file)
    text = transcribe_audio(path, "en")
    print(f"TTS file: {audio_file}")
    print(f"STT heard: {text!r}")
    ok = bool(text) and "hour" in text.lower()
    print("STT ROUND-TRIP:", "PASS" if ok else "FAIL")
except Exception as e:
    print("STT ROUND-TRIP: FAIL —", type(e).__name__, str(e)[:200])
