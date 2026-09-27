# backend/main.py
# ─────────────────────────────────────────────
# Owner  : Poojan
# Branch : feature/poojan-backend
# Task   : FastAPI application entry point
#
# Endpoints to implement:
#   GET  /                              → health check
#   POST /api/business                  → register new business + upload doc
#   GET  /api/business/{business_id}    → get business details
#   GET  /api/businesses                → list all businesses
#   GET  /api/analytics/overview        → system-wide analytics
#   POST /voice/answer/{business_id}    → Vonage webhook (incoming call)
#   POST /voice/event/{business_id}     → Vonage webhook (speech result)
#
# Integrates with:
#   - database.py   (Hardik)
#   - doc_processor.py, llm_service.py, voice_service.py (Henali)
#
# TODO: Add full FastAPI application here
# ─────────────────────────────────────────────
