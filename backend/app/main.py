"""
RailMan AI — FastAPI Application
Serves both the REST API and the web frontend from the same process.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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
    from app.db.mongo import init_db
    await init_db()
    logger.info("RailMan AI started ✓")
    yield
    from app.db import mongo as m
    if m._client:
        m._client.close()
        logger.info("MongoDB client closed")


from app.api import chat, trains, stations, analytics

app = FastAPI(
    title="RailMan AI",
    description="Smart Mumbai Western Railway Assistant",
    version="2.0.0",
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


# ── Health ────────────────────────────────────────────────────────────────── #
@app.get("/health")
async def health():
    from app.db.mongo import get_db
    db = get_db()
    db_ok = False
    if db:
        try:
            await db.command("ping")
            db_ok = True
        except Exception:
            pass
    return {"status": "ok", "db": "connected" if db_ok else "unavailable"}


@app.get("/api")
def api_root():
    return {"app": "RailMan AI", "version": "2.0.0", "docs": "/docs"}


# ── Serve web frontend (catch-all) ────────────────────────────────────────── #
@app.get("/")
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str = ""):
    # Don't intercept /api/* or /docs routes
    if full_path.startswith("api/") or full_path.startswith("docs"):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index)

    return {"error": "Frontend not found"}
