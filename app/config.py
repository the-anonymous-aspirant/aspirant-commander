import os


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://{user}:{password}@{host}/{name}".format(
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "postgres"),
        host=os.environ.get("DB_HOST", "postgres"),
        name=os.environ.get("DB_NAME", "aspirant_online_db"),
    ),
)

TRANSCRIBER_POLL_INTERVAL = int(
    os.environ.get("TRANSCRIBER_POLL_INTERVAL", 30)
)

# The user id to assign to pre-existing processed_valuations rows when the
# owner_user_id column is first added (system_3 #3125). Those rows predate the
# owner column, so the operator supplies the id they belong to at deploy time.
# When unset and un-owned rows exist, the startup migration backfills nothing
# and leaves the column nullable (fail-safe — see app/db_migrate.py), rather
# than crash the service or guess an owner.
_backfill = os.environ.get("VALUATION_BACKFILL_OWNER_ID")
VALUATION_BACKFILL_OWNER_ID = int(_backfill) if _backfill else None

# Least-privilege read-only role the system_3 cell-signal reader connects as
# (system_3 #5539; security ruling on #5542). The scheduled reader must never
# connect as aspirant_admin — it may SELECT from processed_valuations and
# nothing else. app/db_migrate.py::ensure_signal_reader_role provisions it
# idempotently at startup from this secret. When the secret is unset the
# provisioning is a NO-OP (never a default/guessable password), so a deploy
# that has not wired the secret simply has no reader role rather than a weak
# one. The system_3 cron's ASPIRANT_DB_DSN must carry this same password.
SIGNAL_READER_ROLE = "aspirant_signal_ro"
SIGNAL_READER_PASSWORD = os.environ.get("ASPIRANT_SIGNAL_RO_PASSWORD") or None

COMMANDER_VERSION = "1.0.0"
SERVICE_NAME = "commander"
