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
| 🤖 AI Responses | Claude API (Anthropic) |
| 📞 Voice Calls | Vonage telephony integration |
| 🗣️ Speech-to-Text | OpenAI Whisper |
| 🔊 Text-to-Speech | Fish Audio TTS |
| 💾 Persistence | SQLite database |
| 🐳 Containerized | Docker-ready |

---

## 🏗️ Project Structure

```
ai-voice-agent-hackathon/
│
├── backend/                    # FastAPI backend
│   ├── main.py                 # [Poojan]  → API endpoints & Vonage webhooks
│   ├── models.py               # [Hardik]  → SQLAlchemy ORM models
│   ├── database.py             # [Hardik]  → DB session & CRUD helpers
│   ├── init_db.py              # [Hardik]  → DB initialization script
│   ├── doc_processor.py        # [Henali]  → PDF/TXT/Excel text extraction
│   ├── llm_service.py          # [Henali]  → Claude AI response generation
│   ├── voice_service.py        # [Henali]  → Whisper STT + Fish Audio TTS
│   └── test_api.py             # [Suhas]   → Pytest unit tests
│
├── frontend/
│   └── index.html              # [Kiran]   → Business console dashboard
│
├── sample-data/                # Sample knowledge documents for testing
│   ├── sample_kirana_store.txt
│   └── README.md
│
├── tests/                      # Integration & E2E tests
│   └── README.md
│
├── .env.example                # Environment variable template
├── .gitignore
├── Dockerfile                  # [Suhas]   → Docker containerization
├── requirements.txt
└── README.md
```

---

## 👥 Team & Branch Ownership

| Member | Branch | Responsibility |
|---|---|---|
| **Poojan** | `feature/poojan-backend` | FastAPI app, API endpoints, Vonage webhooks |
| **Hardik** | `feature/hardik-database` | SQLAlchemy models, database helpers, init script |
| **Henali** | `feature/henali-voice-services` | Document processor, Claude LLM, Fish Audio TTS |
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

| Service | Get Key From |
|---|---|
| Anthropic Claude | https://console.anthropic.com |
| OpenAI Whisper | https://platform.openai.com |
| Vonage | https://dashboard.nexmo.com |
| Fish Audio TTS | https://fish.audio/app/api-keys |

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

| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | Health check |
| POST | `/api/business` | Register business + upload doc |
| GET | `/api/business/{id}` | Get business details |
| GET | `/api/businesses` | List all businesses |
| GET | `/api/analytics/overview` | System analytics |
| POST | `/voice/answer/{id}` | Vonage incoming call webhook |
| POST | `/voice/event/{id}` | Vonage speech result webhook |

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
