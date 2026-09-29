# 🎤 Hackathon Demo Script (3 minutes, 100% free tiers)

> Everything below works **right now** with free APIs only. No paid services anywhere.

## Before the judges arrive (5-min checklist)

1. Backend running: `cd backend && python -m uvicorn main:app --port 8000`
2. Open **http://localhost:8000** (Home page) in one tab, and the dashboard in another
3. Have the sample files ready: `sample-data/sample_bmw_showroom.txt`, `frontend/sample_call_list.csv`
4. Mic ready (laptop mic is fine). Test volume once.
5. Optional: create your demo account in advance so signup is instant on stage

## The 3-minute story

### 0:00 — The problem (15 sec)
> "Small businesses miss customer calls every day. Hiring a receptionist costs ₹15,000/month. What if every call got answered — in the owner's language — instantly?"

### 0:15 — Sign up + wizard (45 sec) — *dashboard tab*
1. Sign up with any email → dashboard
2. Wizard: pick 🚗 Showroom → "AutoVista Motors" → English → city/hours →
   products: `BMW 3 Series Rs 62 lakh, X5 Rs 49 lakh, test drive free with 1 day notice`
3. Skip the document step → **"Your agent is ready!"**
> "The owner answered 4 friendly questions. No uploads needed, no technical anything."

### 1:00 — 🎤 THE WOW MOMENT: talk to the agent live (60 sec) — *Try Agent tab*
1. Press the mic: **"What is the price of the 3 Series?"**
2. Meera replies **in her own voice**: *"Sure ji, the 3 Series is sixty-two lakh…"*
3. Follow up in Hindi: **"टेस्ट ड्राइव कैसे बुक करें?"** → she switches to Hindi
4. Then Gujarati. Judges just watched one agent fluently handle 3 languages.
> "Same brain, same voice a phone caller gets. Groq runs the AI, Whisper listens, Edge TTS speaks — all free tiers."

### 2:00 — The business gets a lead (30 sec)
1. Tell Meera: **"I'm Raj, 9876543210, book a test drive tomorrow 5pm"**
2. Open **Call Insights** → the lead is already there: name, number, intent "test drive", booked "tomorrow 5pm", full transcript
> "The owner didn't lift a finger. The agent captured a sales-ready lead mid-conversation."

### 2:30 — Outbound: the agent calls customers (30 sec) — *Call Customers tab*
1. Upload `sample_call_list.csv` (5 customers, each with a reason)
2. **Start calling all** → progress fills live
3. Show a completed row → open Call Insights → transcript: *"Hello Rajesh ji! This is Meera from AutoVista Motors, I wanted to personally tell you about the X5 exchange offer…"* + the captured lead
> "The agent doesn't just answer — it hunts. Batch-calls the owner's whole customer list and writes every lead into the CRM."

### 2:50 — Close (10 sec)
> "One document or four answers — any business gets a tireless, multilingual, human-sounding receptionist that captures every lead. Free tier stack: Groq + Whisper + Edge TTS."

## Judge Q&A cheatsheet

- **"Is it really the same voice as the phone?"** — Yes: greeting, answers, farewell all use one Edge neural voice, sanitized so no markdown/emojis are ever spoken.
- **"Can it actually place real phone calls?"** — Yes — Vonage Voice API is fully wired (JWT auth, dialer, campaigns). The on-stage path stays in-browser so the demo never depends on venue network/telephony.
- **"What stops the caller from detecting it's AI?"** — Persona prompts (spoken-style numbers, fillers like "ji", 1–3 sentences), it mirrors the caller's language, and it never self-identifies as AI — it offers a callback like a human receptionist.
- **"Scale?"** — SQLite → Postgres is a config change; the AI stack is stateless HTTP. Rate limits are the real ceiling; Groq's free tier handles a solid demo volume.
- **"Cost per month?"** — ₹0. Groq free tier, Edge TTS free, Whisper free on Groq. Vonage only if you want PSTN calling (~€0.85/mo number + per-minute).

## If something breaks on stage

| Symptom | Fix |
|---|---|
| Mic doesn't respond | Type in the box — same brain, still impressive |
| LLM hiccup / fallback message | Just ask again; the persona recovers |
| Audio silent | Check tab isn't muted; click any bubble replay via "Try again" |
| Rate limit hit (30 req/min) | Wait 10s; demos rarely hit it |
