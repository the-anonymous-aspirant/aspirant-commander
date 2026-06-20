import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import COMMANDER_VERSION, SERVICE_NAME, TRANSCRIBER_POLL_INTERVAL
from app.database import Base, SessionLocal, engine
from app.poller import poll_transcriptions
from app import routes
from app.valuation_statement import routes as valuation_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)

_polling_task: asyncio.Task | None = None


async def _background_polling_loop():
    """Run poll_transcriptions at the configured interval."""
    logger.info(
        "Background polling started (interval=%ds).", TRANSCRIBER_POLL_INTERVAL
    )
    while True:
        try:
            db = SessionLocal()
            try:
                count = poll_transcriptions(db)
                if count > 0:
                    logger.info("Polling cycle processed %d message(s).", count)
            finally:
                db.close()
        except Exception as exc:
            logger.error("Polling cycle error: %s", exc)

        await asyncio.sleep(TRANSCRIBER_POLL_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _polling_task

    try:
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables ready.")

        _polling_task = asyncio.create_task(_background_polling_loop())
        routes.polling_active = True
    except Exception as exc:
        logger.warning("Lifespan startup skipped (likely test mode): %s", exc)

    yield

    logger.info("Shutting down...")
    if _polling_task is not None:
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
    routes.polling_active = False
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Commander Service",
    description="Parse completed voice transcriptions for structured commands and store extracted tasks.",
    version=COMMANDER_VERSION,
    lifespan=lifespan,
)

app.include_router(routes.router)
app.include_router(valuation_routes.router)
