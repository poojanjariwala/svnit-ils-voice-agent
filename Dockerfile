# Dockerfile
# ─────────────────────────────────────────────
# Owner  : Buffy
# Note   : CMD must run from /app/backend. main.py uses bare imports
#         ("from database import ..."), so backend/ has to be on sys.path —
#         `uvicorn backend.main:app` fails with ModuleNotFoundError.
# ─────────────────────────────────────────────
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

# Bare imports resolve relative to here; AUDIO_DIR is relative too.
WORKDIR /app/backend
RUN mkdir -p audio_files

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# --proxy-headers lets Twilio signature validation see the real https:// host
# behind Render's proxy instead of the internal http:// address.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]