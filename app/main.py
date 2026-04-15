import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.models import init_db
from app.routers.admin import limiter, router as admin_router
from app.routers.api import router as api_router
from app.routers.public import router as public_router
from app.scheduler import scheduler
from app.settings_store import get_setting, set_setting
from app.suppliers import run_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("main")
settings = get_settings()

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if not await get_setting("markup_multiplier", ""):
        await set_setting("markup_multiplier", str(settings.markup_multiplier))
    scheduler.add_job(
        run_sync, "interval",
        minutes=settings.sync_interval_minutes,
        id="sync_alstyle",
        next_run_time=datetime.now(),
    )
    scheduler.start()
    logger.info("PressPlay started, sync every %d min", settings.sync_interval_minutes)
    yield
    scheduler.shutdown()


from app.version import VERSION

app = FastAPI(title="PressPlay.kz", version=VERSION, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(public_router)
app.include_router(admin_router)
app.include_router(api_router)
