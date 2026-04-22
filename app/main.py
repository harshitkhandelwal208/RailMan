"""
RailMan AI — FastAPI Application (v4)
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
    from app.services.llm_runtime import get_runtime_status
    from app.services.knowledge_base import get_knowledge_stats
    await init_db()
    rt = get_runtime_status()
    kb = get_knowledge_stats()
    logger.info(
        "RailMan AI v4 started ✓ | providers=%s | local_model=%s | kb_docs=%d",
        ",".join(rt["provider_order"]),
        "✓" if rt["local_model_exists"] else "✗ (not found)",
        kb["total_documents"],
    )
    yield
    await close_db()


from app.api import chat, trains, stations, analytics
from app.api import auth

app = FastAPI(
    title="RailMan AI",
    description="Smart Mumbai Western, Central & Harbour Railway Assistant",
    version="4.0.0",
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


@app.get("/health")
async def health():
    """
    Comprehensive health check — DB connectivity, LLM runtime, knowledge base.
    """
    import time
    from app.db.trains_db import get_db as get_trains_db
    from app.db.chat_db   import get_db as get_chat_db
    from app.services.llm_runtime   import get_runtime_status
    from app.services.knowledge_base import get_knowledge_stats

    # ── DB connectivity ──────────────────────────────────────────────────────
    trains_ok = False
    chat_ok   = False

    trains_db = get_trains_db()
    if trains_db:
        try:
            t0 = time.monotonic()
            await trains_db.command("ping")
            trains_ping_ms = round((time.monotonic() - t0) * 1000, 1)
            trains_ok = True
        except Exception:
            trains_ping_ms = None
    else:
        trains_ping_ms = None

    chat_db = get_chat_db()
    if chat_db:
        try:
            t0 = time.monotonic()
            await chat_db.command("ping")
            chat_ping_ms = round((time.monotonic() - t0) * 1000, 1)
            chat_ok = True
        except Exception:
            chat_ping_ms = None
    else:
        chat_ping_ms = None

    # ── LLM runtime ──────────────────────────────────────────────────────────
    runtime = get_runtime_status()

    # ── Knowledge base ───────────────────────────────────────────────────────
    kb = get_knowledge_stats()

    return {
        "status":  "ok",
        "version": "4.0.0",
        "database": {
            "trains": {
                "status":  "connected" if trains_ok else "unavailable",
                "ping_ms": trains_ping_ms,
            },
            "chat": {
                "status":  "connected" if chat_ok else "unavailable",
                "ping_ms": chat_ping_ms,
            },
            "any_connected": trains_ok or chat_ok,
        },
        "llm_runtime":    runtime,
        "knowledge_base": kb,
    }


@app.get("/ping", response_class=PlainTextResponse)
async def ping():
    """Lightweight liveness check for UptimeRobot.
    Returns plain text 'ok' — avoids Cloudflare bot challenges on HTML pages."""
    return "ok"


@app.get("/api")
def api_root():
    return {"app": "RailMan AI", "version": "4.0.0", "docs": "/docs"}


@app.post("/api/admin/invalidate_cache")
async def invalidate_knowledge_cache():
    """
    Hot-reload the knowledge base without restarting the server.

    After editing chatbot_knowledge.json or chatbot_dialogues.json,
    call this endpoint to clear the LRU cache so the next request
    picks up the updated files automatically.
    """
    from app.services.knowledge_base import invalidate_cache
    invalidate_cache()
    from app.services.knowledge_base import get_knowledge_stats
    return {
        "status": "ok",
        "message": "Knowledge base cache invalidated. Next request will reload from disk.",
        "knowledge_base": get_knowledge_stats(),
    }



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
