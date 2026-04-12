"""
MongoDB Atlas — Full Integration Layer
=======================================
- Async Motor client with connection pooling
- TLS fix for SSL handshake errors (tlsAllowInvalidCertificates=True)
- Startup init: seeds stations + trains if collections empty
- Collections: stations, trains, live_positions, logs, recommendations, feedback
- Graceful degradation: every function works if DB unavailable
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

try:
    import motor.motor_asyncio
    from pymongo import ASCENDING, DESCENDING
    _MOTOR_OK = True
except ImportError:
    _MOTOR_OK = False
    logger.warning("motor/pymongo not installed — MongoDB disabled")

_client = None
_db     = None
_DATA   = Path(__file__).parent.parent / "data"


def get_db():
    """Return Motor database handle, or None if unavailable."""
    global _client, _db
    if _db is not None:
        return _db
    if not _MOTOR_OK:
        return None
    uri = os.getenv("MONGODB_URI", "").strip()
    if not uri:
        return None
    try:
        _client = motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            maxPoolSize=20,
            minPoolSize=2,
            # ── SSL fix for Atlas TLS handshake errors ──────────────────
            tls=True,
            tlsAllowInvalidCertificates=True,
        )
        _db = _client[os.getenv("MONGODB_DB", "railman")]
        logger.info("MongoDB: client created")
        return _db
    except Exception as e:
        logger.error(f"MongoDB client creation failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────── #
# Startup                                                                       #
# ─────────────────────────────────────────────────────────────────────────── #
async def init_db():
    db = get_db()
    if db is None:
        logger.info("MongoDB: skipping init (no URI or motor unavailable)")
        return
    try:
        await db.command("ping")
        logger.info("MongoDB: ping OK ✓")
    except Exception as e:
        logger.error(f"MongoDB: ping failed — {e}")
        return
    await _ensure_indexes(db)
    await _seed_static_data(db)
    logger.info("MongoDB: init complete ✓")


async def _ensure_indexes(db):
    try:
        await db.stations.create_index("id", unique=True)
        await db.stations.create_index("index")
        await db.trains.create_index("id", unique=True)
        await db.trains.create_index([("type", ASCENDING), ("direction", ASCENDING)])
        await db.trains.create_index([("departs_hour", ASCENDING), ("departs_minute", ASCENDING)])
        await db.live_positions.create_index("train_id", unique=True)
        await db.live_positions.create_index("updated_at", expireAfterSeconds=60)
        await db.logs.create_index("session_id")
        await db.logs.create_index([("timestamp", DESCENDING)])
        await db.recommendations.create_index("session_id")
        await db.recommendations.create_index([("timestamp", DESCENDING)])
        await db.recommendations.create_index(
            [("request.source", ASCENDING), ("request.destination", ASCENDING)]
        )
        await db.feedback.create_index("session_id")
        await db.feedback.create_index([("rating", ASCENDING)])
        logger.info("MongoDB: indexes ensured")
    except Exception as e:
        logger.warning(f"MongoDB: index warning — {e}")


async def _seed_static_data(db):
    count = await db.stations.count_documents({})
    if count == 0:
        with open(_DATA / "stations.json") as f:
            stations = json.load(f)
        await db.stations.insert_many(stations)
        logger.info(f"MongoDB: seeded {len(stations)} stations")
    else:
        logger.info(f"MongoDB: stations already seeded ({count})")

    count = await db.trains.count_documents({})
    if count == 0:
        with open(_DATA / "trains.json") as f:
            trains = json.load(f)
        await db.trains.insert_many(trains)
        logger.info(f"MongoDB: seeded {len(trains)} trains")
    else:
        logger.info(f"MongoDB: trains already seeded ({count})")


# ─────────────────────────────────────────────────────────────────────────── #
# Stations                                                                      #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_stations() -> List[dict]:
    db = get_db()
    if db is None:
        return _load_json("stations.json")
    try:
        cursor = db.stations.find({}, {"_id": 0}).sort("index", ASCENDING)
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.warning(f"get_stations: {e}")
        return _load_json("stations.json")


# ─────────────────────────────────────────────────────────────────────────── #
# Trains                                                                        #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_all_trains() -> List[dict]:
    db = get_db()
    if db is None:
        return _load_json("trains.json")
    try:
        cursor = db.trains.find({}, {"_id": 0})
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.warning(f"get_all_trains: {e}")
        return _load_json("trains.json")


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


# ─────────────────────────────────────────────────────────────────────────── #
# Logs                                                                          #
# ─────────────────────────────────────────────────────────────────────────── #
async def log_query(session_id: str, message: str, response: dict):
    db = get_db()
    if db is None:
        return
    try:
        await db.logs.insert_one({
            "session_id": session_id,
            "message":    message,
            "response":   response,
            "timestamp":  datetime.utcnow(),
        })
    except Exception as e:
        logger.debug(f"log_query: {e}")


async def get_recent_queries(limit: int = 50) -> List[dict]:
    db = get_db()
    if db is None:
        return []
    try:
        cursor = db.logs.find(
            {}, {"_id": 0, "session_id": 1, "message": 1, "timestamp": 1}
        ).sort("timestamp", DESCENDING).limit(limit)
        return await cursor.to_list(length=None)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────── #
# Recommendations                                                               #
# ─────────────────────────────────────────────────────────────────────────── #
async def log_recommendation(session_id: str, req: dict, rec: dict):
    db = get_db()
    if db is None:
        return
    try:
        await db.recommendations.insert_one({
            "session_id":     session_id,
            "request":        req,
            "recommendation": rec,
            "timestamp":      datetime.utcnow(),
        })
    except Exception as e:
        logger.debug(f"log_recommendation: {e}")


async def get_popular_routes(limit: int = 10) -> List[dict]:
    db = get_db()
    if db is None:
        return []
    try:
        pipeline = [
            {"$group": {
                "_id": {"source": "$request.source", "destination": "$request.destination"},
                "count": {"$sum": 1},
            }},
            {"$sort": {"count": DESCENDING}},
            {"$limit": limit},
            {"$project": {"source": "$_id.source", "destination": "$_id.destination", "count": 1, "_id": 0}},
        ]
        return await db.recommendations.aggregate(pipeline).to_list(length=None)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────── #
# Feedback                                                                      #
# ─────────────────────────────────────────────────────────────────────────── #
async def save_feedback(session_id: str, rating: int, comment: Optional[str]):
    db = get_db()
    if db is None:
        return
    try:
        await db.feedback.insert_one({
            "session_id": session_id,
            "rating":     rating,
            "comment":    comment,
            "timestamp":  datetime.utcnow(),
        })
    except Exception as e:
        logger.debug(f"save_feedback: {e}")


async def get_feedback_stats() -> dict:
    db = get_db()
    if db is None:
        return {"avg_rating": None, "total": 0}
    try:
        pipeline = [{"$group": {"_id": None, "avg_rating": {"$avg": "$rating"}, "total": {"$sum": 1}}}]
        result = await db.feedback.aggregate(pipeline).to_list(length=1)
        if result:
            return {"avg_rating": round(result[0]["avg_rating"], 2), "total": result[0]["total"]}
        return {"avg_rating": None, "total": 0}
    except Exception:
        return {"avg_rating": None, "total": 0}


# ─────────────────────────────────────────────────────────────────────────── #
# Analytics                                                                     #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_analytics() -> dict:
    db = get_db()
    base = {
        "popular_routes": await get_popular_routes(),
        "feedback_stats": await get_feedback_stats(),
        "recent_queries": await get_recent_queries(10),
    }
    if db is None:
        return {**base, "db_connected": False}
    try:
        base["db_connected"]         = True
        base["total_queries"]        = await db.logs.count_documents({})
        base["total_recommendations"]= await db.recommendations.count_documents({})
        base["total_feedback"]       = await db.feedback.count_documents({})
        base["active_trains_live"]   = await db.live_positions.count_documents({})
        return base
    except Exception:
        return {**base, "db_connected": False}


# ─────────────────────────────────────────────────────────────────────────── #
# JSON fallback                                                                 #
# ─────────────────────────────────────────────────────────────────────────── #
def _load_json(filename: str) -> List[dict]:
    try:
        with open(_DATA / filename) as f:
            return json.load(f)
    except Exception:
        return []
