# backend/conftest.py
# ─────────────────────────────────────────────
# Owner  : Buffy
# Task   : Keep the test run out of the real database.
#
# Why: database.py builds its SQLAlchemy engine from DATABASE_URL at
# import time. Without this, every `pytest` run wrote users, businesses,
# leads and outbound-call rows into the production
# business_voice_agents.db — which is how the Insights sheet filled up
# with fixture leads like "Raj Patel / BMW X1 test drive".
#
# How: pytest loads conftest.py before it imports any test module, so
# setting the env var here lands before `main` → `database` is imported.
# ─────────────────────────────────────────────
import os
import sys
import pathlib
import tempfile

# A fresh file per run, outside the repo so it never gets committed.
_TEST_DB = pathlib.Path(tempfile.gettempdir()) / "voxagent_pytest.db"
if _TEST_DB.exists():
    try:
        _TEST_DB.unlink()
    except OSError:
        pass

# SQLAlchemy wants forward slashes, even for a Windows absolute path.
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"

# The /twilio/* webhook tests assert that unsigned and forged requests are
# rejected with 403. twilio_security.verify_twilio_request() skips verification
# entirely when TWILIO_AUTH_TOKEN is empty (so a local dev box without a .env
# still demos), which would make those tests assert nothing. Pin a token here
# so the signature path is genuinely exercised. load_dotenv() in main.py does
# not override existing vars, and _tw() reads the same env var to sign its
# requests, so signing stays consistent. Set only if the environment hasn't
# already provided one.
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-auth-token-0123456789abcdef")

# Only init_db.py ever called create_all(), so a brand-new file has no
# tables at all — the suite used to pass only because it inherited the
# already-populated production file. Build the schema explicitly now.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from models import Base              # noqa: E402
from database import engine, migrate_missing_columns  # noqa: E402

Base.metadata.create_all(bind=engine)
migrate_missing_columns()


def pytest_sessionfinish(session, exitstatus):
    """Clean up after a green run; keep the DB when a test failed so it can be inspected."""
    if exitstatus != 0 or not _TEST_DB.exists():
        return
    try:
        engine.dispose()   # release SQLite's handle first — Windows can't unlink an open file
        _TEST_DB.unlink()
    except OSError as exc:
        print(f"\nwarning: left temp test DB at {_TEST_DB} ({exc})")