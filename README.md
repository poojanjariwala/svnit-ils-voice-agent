# VoxAgent

A phone agent for small businesses. Meera answers your calls in **Hindi, Gujarati or English**,
quotes your real prices and hours, writes down who wanted what — and calls your customer list
for you.

Built as a hackathon entry; it now runs end to end on a Twilio trial number.

---

## What it does

| | |
|---|---|
| **Multilingual voice** | Hindi / Gujarati / English, mid-call code-switching. Groq Whisper for speech-to-text, Microsoft Edge TTS for speech out — both free. |
| **Your knowledge, not a generic bot** | Upload a price list, brochure or menu (PDF / TXT / Excel). Meera answers from it. |
| **Inbound calls** | Your Twilio number rings, Meera picks up and talks. |
| **Outbound calls** | Dial one customer, or upload a CSV and let her work through the whole list. |
| **Automatic CRM** | Every call that shows interest becomes a lead with a name, number, intent and notes. Full transcripts are kept. |
| **Try it without a phone** | Talk to your agent in the browser by voice or typing. Free, no telephony needed. |
| **Self-serve accounts** | Email signup, owner-scoped agents, bearer-token auth. One owner never sees another's agents, leads or calls. |

---

## Quick start

### 1. Install

```bash
git clone https://github.com/poojanjariwala/svnit-ils-voice-agent.git
cd svnit-ils-voice-agent
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Mac/Linux
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

You need two things in `.env`:

- **`GROQ_API_KEY`** — drives both the LLM and speech-to-text. Free tier: https://console.groq.com/keys
- **`SECRET_KEY`** — signs login tokens. Generate one:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

Twilio credentials are only needed for **real phone calls**. Everything else — accounts, the
wizard, the browser Try-agent, the CRM — works without them.

### 3. Initialise the database

```bash
cd backend
python init_db.py
```

The app also creates and migrates the schema itself on startup, so this step is only really
needed to get an empty database before the first boot.

### 4. Run

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000** — the app serves its own frontend, so don't open the HTML
files directly.

Sign up, walk the wizard, and your agent is live. Click **Talk to agent** to try it immediately.

---

## Making real phone calls

Inbound and outbound calls need a **publicly reachable URL** — your provider has to call your
machine back, and it cannot reach `localhost`. That means a tunnel.

```bash
# terminal 1 — the app
cd backend
python -m uvicorn main:app --port 8000

# terminal 2 — the tunnel
cd backend
python start_tunnel.py          # prints the public URL and checks it is reachable
```

Then copy that printed URL into `PUBLIC_BASE_URL` in `.env` and restart the app. The webhook
URLs the provider calls are all derived from that one value, so if it is wrong nothing will
connect.

Add your Twilio credentials to `.env`:

| Variable | Where to find it |
|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio console → Account SID |
| `TWILIO_AUTH_TOKEN` | Twilio console → Auth Token |
| `TWILIO_PHONE_NUMBER` | Your number, in E.164 form (`+15551234567`) |

Get a number and credits at https://www.twilio.com/try-twilio. Trial accounts can only call
numbers you verify by SMS in the console — verify yours before testing outbound.

Twilio is called over plain HTTPS, so there is no `twilio` package to install. Twilio is the
only telephony provider; everything lives in [phone_service.py](backend/phone_service.py) and
the four `/twilio/*` webhooks.

### Numbers are normalised to E.164

Every number is put through `to_e164()` in `phone_service.py` before it is dialled, so you can
type `98765 43210` and it still connects. A bare 10-digit number gets `DEFAULT_COUNTRY_CODE`
prefixed (`91` by default — change it to your own). Anything you write with an explicit
country code is passed through untouched. Twilio's own rejection codes are translated into a
sentence you can act on, and `21219` / `21215` now explicitly say *"trial account, number not
verified"* instead of blaming the number you were calling.

### Public URL precedence — read this before blaming a dead host

`_public()` in `main.py` resolves the public URL in this order:

1. **`backend/public_url.txt`**, if it exists and is non-empty
2. `PUBLIC_BASE_URL` from `.env`
3. `http://localhost:8000`

The override file exists so a new tunnel URL can apply **without restarting the server**.
`start_tunnel.py` writes it once the tunnel reports its URL, so starting the tunnel also
points the app at it — one step, no restart.

This matters because localtunnel does not always honour a requested subdomain. It has handed
back `curly-fish-50.loca.lt` while `public_url.txt` still said `public-things-appear`, and the
app kept building Twilio webhook URLs against a host that no longer existed — so every call
silently 503'd. **Always confirm `public_url.txt` matches the URL the tunnel actually reported**;
if you are not sure, delete the file and fall back to `.env`.

### Startup self-check

About two seconds after boot the app probes `PUBLIC_BASE_URL` (3 attempts, 5s each) and logs
`✅ PUBLIC_BASE_URL reachable` or a loud ⚠️ warning if every attempt failed — that warning is
the signal that inbound calls will not connect. `GET /api/phone/status` does the same kind of
real probe against Twilio (memoised for 30s) instead of reporting "connected" just because
credentials exist.

---

## Database

SQLite by default — zero setup, and the file lives beside the app:

```bash
DATABASE_URL=sqlite:///./business_voice_agents.db
```

Postgres is supported for anything that outlives a machine, which matters because cloud hosts
give you an ephemeral disk and wipe SQLite on every restart:

```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

`postgres://` is accepted and rewritten to `postgresql://`. `init_schema()` and
`migrate_missing_columns()` run on every startup, so the target database creates itself on first
boot and picks up new columns after an upgrade — you do not need to run a migration by hand.

To move existing local data across:

```bash
cd backend
python migrate_to_postgres.py --target "postgresql://user:pw@host/db"
```

It reflects the SQLite schema, creates the same schema in the target, and copies rows. It
**refuses to run if the target already has rows in any table**, so you cannot half-overwrite
live data by accident.

---

## Project layout

```
svnit-ils-voice-agent/
├── backend/
│   ├── main.py            # FastAPI app: auth, agents, telephony webhooks, CRM, campaigns
│   ├── auth.py            # Signup/login, PBKDF2 hashing, bearer tokens
│   ├── models.py          # SQLAlchemy models (User, Business, Lead, Call, OutboundCall, Campaign)
│   ├── database.py        # Engine, session, owner-scoped CRUD helpers, schema creation/migration
│   ├── phone_service.py   # Twilio call placement, E.164 normalisation, trial-error humanising
│   ├── twilio_security.py # X-Twilio-Signature validation for every /twilio/* webhook
│   ├── campaign_manager.py# Async outbound campaign dialing queue
│   ├── compliance.py      # Consent registry + TRAI calling-hours/DND checks
│   ├── llm_service.py     # Groq response generation + lead extraction
│   ├── voice_service.py   # Groq Whisper STT + Edge TTS
│   ├── doc_processor.py   # PDF / TXT / Excel text extraction
│   ├── start_tunnel.py    # Public tunnel for telephony webhooks; writes public_url.txt
│   ├── twilio_setup.py    # One-time: claim a trial number, write creds to .env
│   ├── twilio_verify.py   # One-time: verify your own number as caller ID (trial accounts)
│   ├── twilio_smoke.py    # Live signed-webhook checks against a running server
│   ├── test_accuracy.py   # Ad-hoc live battery: KB answers + STT round-trip
│   ├── migrate_to_postgres.py  # Copy the local SQLite DB into Postgres
│   ├── init_db.py         # Create tables
│   ├── conftest.py        # Points pytest at a throwaway DB (see Testing)
│   └── test_api.py        # Pytest suite (66 tests)
│
├── frontend/              # Served by FastAPI at /
│   ├── index.html         # Public landing page
│   ├── login.html         # Log in / sign up
│   └── dashboard.html     # Auth-gated console (wizard, try-agent, outbound, CRM)
│
├── tests/                 # Integration tests (unit tests live in backend/test_api.py)
├── sample-data/           # Sample knowledge docs + a sample customer CSV
├── docs/                  # Production economics write-up
├── Dockerfile
├── .dockerignore
├── docker-compose.yml     # FastAPI + Postgres + Redis + Ollama
├── render.yaml            # Render blueprint: web service + Postgres
├── requirements.txt
├── DEMO.md                # 3-minute demo script
└── .env.example
```

---

## Testing

```bash
cd backend
pytest test_api.py -v                       # 66 unit tests, no server needed

python -m uvicorn main:app --port 8000 &    # then, against a live server:
python twilio_smoke.py
```

`test_api.py` stubs the LLM and TTS, so it is hermetic and needs no API keys.
`twilio_smoke.py` hits a **running** server and makes **real** requests — it does not place
calls, but it does consume provider API quota.

**Tests run against a temporary database.** `backend/conftest.py` repoints `DATABASE_URL` at a
file in your temp directory before any app module is imported, creates the schema there, and
deletes the file when the session ends. Without it, `database.py` builds its engine from
`DATABASE_URL` at import time and every `pytest` run writes users, businesses and leads into
your real database — which is how the Insights sheet filled up with fixture leads like
"Raj Patel / BMW X1 test drive". **If you ever move or delete `conftest.py`, re-check that
tests are not writing to `business_voice_agents.db`.** Note that `twilio_smoke.py` bypasses
pytest entirely and *does* hit the real database.

`twilio_smoke.py` signs its webhook requests with the real `twilio-python` algorithm and
asserts that a forged request is rejected, so the signature check is covered by a test rather
than by assertion in a comment.

---

## API

Everything under `/api/*` except signup, login and health needs an
`Authorization: Bearer <token>` header.

**Auth** — `POST /api/auth/signup` · `POST /api/auth/login` · `GET /api/auth/me`

**Agents** — `POST /api/business` (create + optional doc upload) · `GET /api/businesses` ·
`GET /api/business/{id}`

**Try agent (browser, no phone)** — `POST /api/try/{id}/listen` · `POST /api/try/{id}/say` ·
`POST /api/try/{id}/reset`

**CRM** — `GET /api/leads` · `PATCH /api/leads/{id}` · `GET /api/calls` ·
`GET /api/analytics/overview`

**Outbound** — `POST /api/call-now/{id}` · `GET /api/outbound` · `GET /api/phone/status` ·
`POST /api/campaigns/{id}` · `POST /api/campaigns/{id}/start` · `POST /api/campaigns/{id}/stop` ·
`GET /api/campaigns/{id}`

**Webhooks** (called by Twilio; signature-verified) —
`POST /twilio/answer/{id}` · `POST /twilio/outbound-answer/{id}` · `POST /twilio/event/{id}` ·
`POST /twilio/status/{id}`.

`GET /api/health` is public and returns `{"status": "running", "version": ...}`.

---

## Webhook security

All four `/twilio/*` webhooks validate `X-Twilio-Signature` before doing anything
([twilio_security.py](backend/twilio_security.py)). A request with a missing or wrong signature
gets **403**; a correctly signed one is allowed through. `TWILIO_AUTH_TOKEN` is required for the
check to mean anything — if it is unset, requests are allowed and a loud warning is logged at
startup.

The implementation was diffed against the real `twilio-python` `RequestValidator` and matches it
byte for byte on the test cases, including the two details that are easy to get wrong: the
signature covers the exact URL Twilio called (so the port may or may not be present depending on
which side you ask), and behind a proxy the scheme and host come from `X-Forwarded-*` — which is
why the Docker image runs uvicorn with `--proxy-headers`.

### The webhooks always answer

A 5xx from a webhook is what makes a caller hear Twilio's "application error", so every
`/twilio/*` route is wrapped: an unexpected exception still returns **200 + valid TwiML** — a
`<Say>` apology in the business's language — with the stack trace logged server-side. A TTS
failure degrades to `<Say>` carrying the same text instead of `<Play>`, and the blocking Groq
calls run off the event loop so a slow LLM response cannot breach Twilio's 15-second webhook
timeout. Greeting audio is cached per business+language, so a second ring does not synthesise
live.

---

## Docker

```bash
docker build -t voxagent .
docker run -p 8000:8000 --env-file .env voxagent
```

A tunnel is still required for real calls — the container's `localhost` is not reachable from
the phone provider.

Two things about the image are deliberate:

- **`WORKDIR /app/backend` and `CMD ["uvicorn", "main:app", ...]`.** `main.py` uses bare imports
  (`from database import ...`), so `backend/` has to be on `sys.path`. Running
  `uvicorn backend.main:app` from `/app` fails with `ModuleNotFoundError`.
- **`.dockerignore` excludes `backend/public_url.txt`.** `_public()` reads that file *before*
  `PUBLIC_BASE_URL`, so shipping it would override the deployed URL and point every Twilio
  webhook back at a dead localtunnel host.

---

## Deploying to Render

[render.yaml](render.yaml) is a Render blueprint with a free web service (Docker runtime,
`singapore` region, `/api/health` as the health check) and a free Postgres instance wired in via
`fromDatabase`. `PUBLIC_BASE_URL` comes from `RENDER_EXTERNAL_URL`, so the deployed URL is
always the right one with no copy-paste.

Push the repo to GitHub, then in Render choose **New + → Blueprint** and select the repo. Render
reads the file and prompts you for the four secrets marked `sync: false`
(`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `GROQ_API_KEY`).
`SECRET_KEY` is generated for you. `autoDeploy` is `false` on purpose — opt in once you have seen
it working.

Two free-tier caveats to understand before you rely on it:

- **Free Postgres expires after 30 days.** You will need to move to a paid instance or export
  your data before then.
- **The free web service sleeps when idle**, and it will not answer a Twilio callback while
  asleep. An inbound call to a sleeping free instance is dropped.

To carry local data to the deployed database, run `migrate_to_postgres.py` against the Render
connection string.

---

## What's implemented beyond the core loop

- **Bulk campaign manager** ([campaign_manager.py](backend/campaign_manager.py)) — works through
  an uploaded CSV list with live progress, stopping cleanly with a per-row reason when a dial
  fails.
- **TRAI compliance helpers** ([compliance.py](backend/compliance.py)) — consent registry,
  08:00–21:00 calling-hours check, and multilingual call-recording disclosure text.
- **Self-hosted stack** ([docker-compose.yml](docker-compose.yml)) — FastAPI + Postgres 15 +
  Redis 7 + Ollama, for running without cloud dependencies.
- **Unit economics** ([docs/PRODUCTION_ECONOMICS.md](docs/PRODUCTION_ECONOMICS.md)) — cost model
  for running this per-business.

---

## Known gaps


Things that are genuinely not done, stated plainly:

- **The Docker image has never been built**, and the Postgres `INSERT` path has never run
  against a live Postgres — no local Postgres was available and the Docker daemon was not
  running. What *is* verified: all 8 models compile to Postgres DDL, URL normalisation, engine
  construction, schema reflection with matching column counts, and the migration guards. Treat
  the first deploy as the real test.
- **Tunnel drift is still the most common cause of "it was working yesterday".** The startup
  probe above will *tell* you the URL is dead, and `start_tunnel.py` now rewrites
  `public_url.txt` itself — but localtunnel still mints a new random URL whenever it restarts,
  so a dropped tunnel needs the script re-running.
- **Call status can be lost.** If a status callback never arrives, the row stays in `calling`.
  Reads reconcile anything older than the 15-minute call window, but there is no retry.
- **Sessions are in process memory.** `CALL_SESSIONS` does not survive a restart and will not
  work behind more than one worker.
- **The production SQLite database still contains fixture data** — 104 leads and 703 businesses
  (counts as of 2026-10-04), the vast majority left over from test and smoke runs. They are
  excluded from the Insights sheet, not deleted.
- **Tailwind runs from the CDN** in development, which is not suitable for production.
- **The LLM extraction path is not fully proven.** Groq responds from this machine, but the
  regex fallback is the only path with an end-to-end test behind it.

## License

MIT