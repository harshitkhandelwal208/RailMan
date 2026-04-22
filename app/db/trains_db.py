"""
MongoDB — Trains Database (v3)
================================
Handles everything related to the railway network:
  stations, trains, live_positions

URI env var : MONGODB_TRAINS_URI   (fallback: MONGODB_URI)
DB name     : MONGODB_TRAINS_DB    (default: railman_trains)

Graceful degradation: every function works if DB unavailable.
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.db.mongo_utils import build_mongo_client_kwargs, resolve_mongo_uri

logger = logging.getLogger(__name__)

try:
    import motor.motor_asyncio
    from pymongo import ASCENDING, DESCENDING
    _MOTOR_OK = True
except ImportError:
    _MOTOR_OK = False
    logger.warning("motor/pymongo not installed — Trains DB disabled")

_client = None
_db     = None
_DATA   = Path(__file__).parent.parent / "data"


def get_db():
    """Return Motor trains database handle, or None if unavailable."""
    global _client, _db
    if _db is not None:
        return _db
    if not _MOTOR_OK:
        return None
    # Prefer dedicated trains URI, fall back to shared URI
    uri = resolve_mongo_uri("MONGODB_TRAINS_URI")
    if not uri:
        return None
    try:
        _client = motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            **build_mongo_client_kwargs(uri),
        )
        db_name = os.getenv("MONGODB_TRAINS_DB", "railman_trains")
        _db = _client[db_name]
        logger.info(f"Trains DB: client created → {db_name}")
        return _db
    except Exception as e:
        logger.error(f"Trains DB client creation failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────── #
# Startup                                                                       #
# ─────────────────────────────────────────────────────────────────────────── #
async def init_db():
    db = get_db()
    if db is None:
        logger.info("Trains DB: skipping init (no URI or motor unavailable)")
        return
    try:
        await db.command("ping")
        logger.info("Trains DB: ping OK ✓")
    except Exception as e:
        logger.error(f"Trains DB: ping failed — {e}")
        return
    await _ensure_indexes(db)
    await _seed_static_data(db)
    logger.info("Trains DB: init complete ✓")


async def _ensure_indexes(db):
    try:
        await db.stations.create_index("id", unique=True)
        await db.stations.create_index("index")

        await db.trains.create_index("id", unique=True)
        await db.trains.create_index([("type", ASCENDING), ("direction", ASCENDING)])
        await db.trains.create_index([("departs_hour", ASCENDING), ("departs_minute", ASCENDING)])

        await db.live_positions.create_index("train_id", unique=True)
        await db.live_positions.create_index("updated_at", expireAfterSeconds=60)

        logger.info("Trains DB: indexes ensured")
    except Exception as e:
        logger.warning(f"Trains DB: index warning — {e}")


async def _sync_static_collection(db, collection_name: str, filename: str) -> int:
    """Replace stale static records in Mongo with the bundled JSON dataset."""
    collection = getattr(db, collection_name)
    with open(_DATA / filename, encoding="utf-8") as f:
        items = json.load(f)

    seen_ids = []
    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue
        seen_ids.append(item_id)
        await collection.replace_one({"id": item_id}, item, upsert=True)

    if seen_ids:
        await collection.delete_many({"id": {"$nin": seen_ids}})
    return len(seen_ids)


async def _seed_static_data(db):
    stations_synced = await _sync_static_collection(db, "stations", "stations.json")
    trains_synced = await _sync_static_collection(db, "trains", "trains.json")
    logger.info(
        "Trains DB: synced static data (stations=%d, trains=%d)",
        stations_synced,
        trains_synced,
    )


# ─────────────────────────────────────────────────────────────────────────── #
# Stations                                                                      #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_stations() -> List[dict]:
    # The bundled JSON is the source of truth; MongoDB is only a cache/sync target.
    items = _load_json("stations.json")
    if items:
        return items
    db = get_db()
    if db is None:
        return []
    try:
        cursor = db.stations.find({}, {"_id": 0}).sort("index", ASCENDING)
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.warning(f"get_stations: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────── #
# Trains                                                                        #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_all_trains() -> List[dict]:
    # The bundled JSON is the source of truth; MongoDB is only a cache/sync target.
    items = _load_json("trains.json")
    if items:
        return items
    db = get_db()
    if db is None:
        return []
    try:
        cursor = db.trains.find({}, {"_id": 0})
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.warning(f"get_all_trains: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────── #
# Live positions (TTL 60s)                                                      #
# ─────────────────────────────────────────────────────────────────────────── #
async def upsert_live_position(train_id: str, position: dict):
    db = get_db()
    if db is None:
        return
    try:
        await db.live_positions.update_one(
            {"train_id": train_id},
            {"$set": {**position, "train_id": train_id, "updated_at": datetime.utcnow()}},
            upsert=True,
        )
    except Exception as e:
        logger.debug(f"upsert_live_position: {e}")


async def get_live_positions() -> List[dict]:
    db = get_db()
    if db is None:
        return []
    try:
        cursor = db.live_positions.find({}, {"_id": 0, "updated_at": 0})
        return await cursor.to_list(length=None)
    except Exception:
        return []


async def get_active_trains_count() -> int:
    db = get_db()
    if db is None:
        return 0
    try:
        return await db.live_positions.count_documents({})
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────── #
# JSON fallback                                                                 #
# ─────────────────────────────────────────────────────────────────────────── #
def _load_json(filename: str) -> List[dict]:
    try:
        with open(_DATA / filename) as f:
            return json.load(f)
    except Exception:
        return []
