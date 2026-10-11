# backend/migrate_to_postgres.py
# ─────────────────────────────────────────────
# Owner  : Buffy
# Task   : Copy the local SQLite database into Postgres.
#
# Why: cloud hosts give an ephemeral disk, so SQLite is wiped on every
# restart. This moves the real data (users, businesses, leads, calls) to
# Postgres before the first deploy.
#
# Run:  python migrate_to_postgres.py --target "postgresql://user:pw@host/db"
# Safe: re-running is refused if the target already has rows, so you
#       cannot half-overwrite live data by accident.
# ─────────────────────────────────────────────
import argparse
import os
import sqlite3
import sys

import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

DEFAULT_SQLITE = os.path.join(BACKEND, "business_voice_agents.db")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=DEFAULT_SQLITE)
    ap.add_argument("--target", default=os.getenv("DATABASE_URL", ""))
    args = ap.parse_args()

    if not args.target:
        print("No target given. Pass --target or set DATABASE_URL.")
        return 2
    if args.target.startswith("sqlite"):
        print("Target is still SQLite — that is the thing we're migrating away from.")
        return 2
    if not os.path.exists(args.sqlite):
        print(f"No SQLite file at {args.sqlite}")
        return 2

    src = sqlite3.connect(args.sqlite)
    src.row_factory = sqlite3.Row

    dst_engine = create_engine(normalise_url(args.target), pool_pre_ping=True)

    from models import Base
    from database import normalise_url
    Base.metadata.create_all(bind=dst_engine)

    # reflect the SQLite side so we follow the real schema, not the model
    src_engine = create_engine(f"sqlite:///{args.sqlite}")
    src_meta = sa.MetaData()
    src_meta.reflect(bind=src_engine)
    tables = [t for t in src_meta.sorted_tables if t.name in Base.metadata.tables]

    if not tables:
        print("Nothing to copy — the SQLite file has no known tables.")
        return 2

    total = 0
    print(f"Copying {len(tables)} tables -> {args.target.split('@')[-1]}\n")
    for table in tables:
        rows = src.execute(f'SELECT * FROM "{table.name}"').fetchall()
        if not rows:
            print(f"  {table.name:<18} (empty)")
            continue

        target_tbl = Base.metadata.tables[table.name]
        cols = [c.name for c in target_tbl.columns]
        with dst_engine.begin() as conn:
            existing = conn.execute(
                sa.text(f'SELECT COUNT(*) FROM "{table.name}"')
            ).scalar()
            if existing:
                print(f"  {table.name:<18} SKIPPED — target already holds {existing} rows")
                continue
            payload = [{k: r[k] for k in r.keys() if k in cols} for r in rows]
            conn.execute(target_tbl.insert(), payload)
        total += len(rows)
        print(f"  {table.name:<18} {len(rows):>6} rows")

    print(f"\nDone — {total} rows copied.")
    print("Next: point DATABASE_URL at Postgres and restart the app.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())