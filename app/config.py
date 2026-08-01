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

COMMANDER_VERSION = "1.0.0"
SERVICE_NAME = "commander"
