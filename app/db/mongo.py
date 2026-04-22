"""
MongoDB compatibility shim (v3)
================================
This file exists so that any code still importing from app.db.mongo
continues to work without changes.

All functions are re-exported from the appropriate split database module:
  - app.db.trains_db  →  stations, trains, live_positions
  - app.db.chat_db    →  chat_memory, logs, recommendations, feedback, users, rate_limits

For new code, import directly from trains_db or chat_db.
"""

# ── Trains DB ──────────────────────────────────────────────────────────────
from app.db.trains_db import (
    get_db          as _get_trains_db,
    init_db         as _init_trains_db,
    get_stations,
    get_all_trains,
    upsert_live_position,
    get_live_positions,
    get_active_trains_count,
)

# ── Chat DB ────────────────────────────────────────────────────────────────
from app.db.chat_db import (
    get_db          as _get_chat_db,
    init_db         as _init_chat_db,
    get_chat_history,
    append_chat_memory,
    clear_chat_memory,
    get_user_by_email,
    get_user_by_id,
    create_user,
    update_user_login,
    check_rate_limit,
    log_query,
    get_recent_queries,
    log_recommendation,
    get_popular_routes,
    save_feedback,
    get_feedback_stats,
    get_counts,
)

import logging
logger = logging.getLogger(__name__)

import app.db.trains_db as _tdb
import app.db.chat_db   as _cdb

_client = None  # kept for backward compat; actual clients live in sub-modules


# ── Combined init / shutdown ───────────────────────────────────────────────
async def init_db():
    """Initialise both databases."""
    await _init_trains_db()
    await _init_chat_db()


async def close_db():
    """Close both database clients."""
    if _tdb._client:
        _tdb._client.close()
        logger.info("Trains DB client closed")
    if _cdb._client:
        _cdb._client.close()
        logger.info("Chat DB client closed")


# ── Combined get_db (returns trains db for backward compat) ───────────────
def get_db():
    """Backward-compat: returns the trains DB handle."""
    return _get_trains_db()


# ── Combined analytics helper ─────────────────────────────────────────────
async def get_analytics() -> dict:
    popular   = await get_popular_routes()
    feedback  = await get_feedback_stats()
    recent    = await get_recent_queries(10)
    counts    = await get_counts()
    live_ct   = await get_active_trains_count()

    trains_db_ok = _get_trains_db() is not None
    chat_db_ok   = _get_chat_db()   is not None

    return {
        "popular_routes":        popular,
        "feedback_stats":        feedback,
        "recent_queries":        recent,
        "active_trains_live":    live_ct,
        "trains_db_connected":   trains_db_ok,
        "chat_db_connected":     chat_db_ok,
        "db_connected":          trains_db_ok or chat_db_ok,
        **counts,
    }
