# 🎙️ AI Voice Agent for Business Customer Care

> Intelligent multilingual AI-powered voice agent — Hackathon Edition

---

## 🚀 Overview

An AI voice agent that provides **24/7 multilingual customer support** through Vonage phone calls. Businesses upload their knowledge document (PDF/TXT/Excel), and the system auto-generates an intelligent phone agent in **Hindi, Gujarati, or English**.

---

## 🌟 Features

| Feature | Description |
|---|---|
| 🌍 Multilingual | Hindi, Gujarati, English support |
| 🤖 AI Responses | Groq API (GPT-OSS 120B) — free tier |
| 📞 Voice Calls | Vonage telephony integration |
| 🗣️ Speech-to-Text | Groq-hosted Whisper — free tier |
| 🔊 Text-to-Speech | Microsoft Edge TTS — free, no API key |
| 👤 Accounts | Email signup/login, owner-scoped agents, bearer-token auth |
| 🏢 Any business | Retail, car showrooms, finance firms, agencies, clinics, real estate |
| 💾 Persistence | SQLite database |
| 🐳 Containerized | Docker-ready |

---

## 🏗️ Project Structure

```
svnit-ils-voice-agent/
│
├── backend/                    # FastAPI backend
│   ├── main.py                 # API endpoints, auth, Vonage webhooks, serves frontend
│   ├── auth.py                 # Signup/login, PBKDF2 hashing, bearer tokens
│   ├── models.py               # SQLAlchemy models (User, Business, Call, …)
│   ├── database.py             # DB session & owner-scoped CRUD helpers
│   ├── init_db.py              # DB initialization script
│   ├── doc_processor.py        # PDF/TXT/Excel text extraction
│   ├── llm_service.py          # Groq LLM response generation
│   ├── voice_service.py        # Groq Whisper STT + Edge TTS
│   └── test_api.py             # Pytest suite (auth, CRUD, webhooks)
│
├── frontend/                   # Served by FastAPI at http://localhost:8000/
│   ├── index.html              # Public home page
│   ├── login.html              # Login + Signup
│   ├── dashboard.html          # Auth-gated agent console
│   └── sample_bmw_showroom.txt # One-click sample doc for the console
│
├── sample-data/                # Sample knowledge documents
│   ├── sample_bmw_showroom.txt #   BMW dealership (models, finance, service)
│   └── sample_kirana_store.txt #   Small retail store
│
├── .env.example                # Environment variable template
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## 👥 Team & Branch Ownership

| Member | Branch | Responsibility |
|---|---|---|
| **Poojan** | `feature/poojan-backend` | FastAPI app, API endpoints, Vonage webhooks |
| **Hardik** | `feature/hardik-database` | SQLAlchemy models, database helpers, init script |
| **Henali** | `feature/henali-voice-services` | Document processor, Groq LLM, Edge TTS |
| **Kiran** | `feature/kiran-frontend` | HTML/CSS/JS dashboard |
| **Suhas** | `feature/suhas-devops` | Tests, Docker, ngrok, deployment |

---

## ⚡ Quick Start

### 1. Clone & Setup

```bash
git clone <repo-url>
cd ai-voice-agent-hackathon
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Initialize Database

```bash
cd backend
python init_db.py
```

### 4. Start Backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

### 5. Open Frontend

Open `frontend/index.html` in your browser (or use Live Server).

---

## 🔑 Required API Keys

| Service | Get Key From | Cost |
|---|---|---|
| Groq (LLM + Whisper STT) | https://console.groq.com/keys | Free tier |
| Microsoft Edge TTS | No key needed (edge-tts) | Free |
| Vonage | https://dashboard.nexmo.com | Free trial credits |

Also set `SECRET_KEY` in `.env` (any long random string) — it signs login tokens.
Generate one: `python -c "import secrets; print(secrets.token_hex(32))"`

---

## 🧪 Running Tests

```bash
cd backend
pytest test_api.py -v
```

---

## 🐳 Docker

```bash
docker build -t ai-voice-agent:latest .
docker run -p 8000:8000 --env-file .env ai-voice-agent:latest
```

---

## 📞 API Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/` | — | Home page |
| GET | `/api/health` | — | Health check |
| POST | `/api/auth/signup` | — | Create account → token |
| POST | `/api/auth/login` | — | Log in → token |
| GET | `/api/auth/me` | Bearer | Who am I |
| POST | `/api/business` | Bearer | Create agent + upload doc |
| GET | `/api/business/{id}` | Bearer | Agent details (owner only) |
| GET | `/api/businesses` | Bearer | Your agents |
| GET | `/api/analytics/overview` | Bearer | Your analytics |
| POST | `/voice/answer/{id}` | — | Vonage incoming call webhook |
| POST | `/voice/event/{id}` | — | Vonage speech result webhook |

Voice webhooks stay unauthenticated by design — Vonage calls them from its own network.

---

## 📋 Merge Order

When integrating, merge branches in this order:
1. `feature/hardik-database`
2. `feature/poojan-backend`
3. `feature/henali-voice-services`
4. `feature/kiran-frontend`
5. `feature/suhas-devops`

---

## 📄 License

MIT License — Hackathon Edition 🏆
