"""
RailMan AI — FastAPI Application (v3)
Serves both the REST API and the web frontend from the same process.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent.parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db.mongo import init_db, close_db
    await init_db()
    logger.info("RailMan AI v3 started ✓")
    yield
    await close_db()


from app.api import chat, trains, stations, analytics
from app.api import auth

app = FastAPI(
    title="RailMan AI",
    description="Smart Mumbai Western Railway Assistant",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────── #
app.include_router(chat.router)
app.include_router(trains.router)
app.include_router(stations.router)
app.include_router(analytics.router)
app.include_router(auth.router)


# ── Health & ping ─────────────────────────────────────────────────────────── #
@app.get("/health")
async def health():
    from app.db.trains_db import get_db as get_trains_db
    from app.db.chat_db   import get_db as get_chat_db

    trains_ok = False
    chat_ok   = False

    trains_db = get_trains_db()
    if trains_db:
        try:
            await trains_db.command("ping")
            trains_ok = True
        except Exception:
            pass

    chat_db = get_chat_db()
    if chat_db:
        try:
            await chat_db.command("ping")
            chat_ok = True
        except Exception:
            pass

    return {
        "status":    "ok",
        "version":   "3.0.0",
        "trains_db": "connected" if trains_ok else "unavailable",
        "chat_db":   "connected" if chat_ok   else "unavailable",
        "db":        "connected" if (trains_ok or chat_ok) else "unavailable",
    }


@app.get("/ping", response_class=PlainTextResponse)
async def ping():
    """Lightweight liveness check for UptimeRobot.
    Returns plain text 'ok' — avoids Cloudflare bot challenges on HTML pages."""
    return "ok"


@app.get("/api")
def api_root():
    return {"app": "RailMan AI", "version": "3.0.0", "docs": "/docs"}


# ── Serve web frontend (catch-all) ────────────────────────────────────────── #
# Must come LAST so it doesn't swallow the routes above.
_EXCLUDED = ("api/", "docs", "health", "ping", "openapi")

@app.get("/")
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str = ""):
    if any(full_path.startswith(p) for p in _EXCLUDED):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index)

    return {"error": "Frontend not found"}
