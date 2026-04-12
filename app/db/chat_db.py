"""
MongoDB — Chat/User Database (v3)
==================================
Handles everything related to the chatbot and users:
  chat_memory, logs, recommendations, feedback, users, rate_limits

URI env var : MONGODB_CHAT_URI   (fallback: MONGODB_URI)
DB name     : MONGODB_CHAT_DB    (default: railman_chat)

Graceful degradation: every function works if DB unavailable.
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List

logger = logging.getLogger(__name__)

try:
    import motor.motor_asyncio
    from pymongo import ASCENDING, DESCENDING
    _MOTOR_OK = True
except ImportError:
    _MOTOR_OK = False
    logger.warning("motor/pymongo not installed — Chat DB disabled")

_client = None
_db     = None

MAX_MEMORY_TURNS = 20   # per session, keep last N turns in MongoDB memory


def get_db():
    """Return Motor chat database handle, or None if unavailable."""
    global _client, _db
    if _db is not None:
        return _db
    if not _MOTOR_OK:
        return None
    # Prefer dedicated chat URI, fall back to shared URI
    uri = os.getenv("MONGODB_CHAT_URI", os.getenv("MONGODB_URI", "")).strip()
    if not uri:
        return None
    try:
        _client = motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            maxPoolSize=20,
            minPoolSize=2,
            tls=True,
            tlsAllowInvalidCertificates=True,
        )
        db_name = os.getenv("MONGODB_CHAT_DB", "railman_chat")
        _db = _client[db_name]
        logger.info(f"Chat DB: client created → {db_name}")
        return _db
    except Exception as e:
        logger.error(f"Chat DB client creation failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────── #
# Startup                                                                       #
# ─────────────────────────────────────────────────────────────────────────── #
async def init_db():
    db = get_db()
    if db is None:
        logger.info("Chat DB: skipping init (no URI or motor unavailable)")
        return
    try:
        await db.command("ping")
        logger.info("Chat DB: ping OK ✓")
    except Exception as e:
        logger.error(f"Chat DB: ping failed — {e}")
        return
    await _ensure_indexes(db)
    logger.info("Chat DB: init complete ✓")


async def _ensure_indexes(db):
    try:
        await db.logs.create_index("session_id")
        await db.logs.create_index([("timestamp", DESCENDING)])

        await db.recommendations.create_index("session_id")
        await db.recommendations.create_index([("timestamp", DESCENDING)])
        await db.recommendations.create_index(
            [("request.source", ASCENDING), ("request.destination", ASCENDING)]
        )

        await db.feedback.create_index("session_id")
        await db.feedback.create_index([("rating", ASCENDING)])

        # Chat memory indexes
        await db.chat_memory.create_index("session_id")
        await db.chat_memory.create_index([("session_id", ASCENDING), ("timestamp", DESCENDING)])
        await db.chat_memory.create_index("timestamp", expireAfterSeconds=86400 * 30)  # 30-day TTL

        # Users / Auth indexes
        await db.users.create_index("email", unique=True)
        await db.users.create_index("google_id", sparse=True)
        await db.users.create_index("github_id", sparse=True)
        await db.users.create_index("created_at")

        # Rate limiting index
        await db.rate_limits.create_index("key", unique=True)
        await db.rate_limits.create_index("reset_at", expireAfterSeconds=0)

        logger.info("Chat DB: indexes ensured")
    except Exception as e:
        logger.warning(f"Chat DB: index warning — {e}")


# ─────────────────────────────────────────────────────────────────────────── #
# Chat Memory (persistent per session)                                          #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_chat_history(session_id: str, limit: int = MAX_MEMORY_TURNS) -> List[dict]:
    """Retrieve recent conversation history for a session from MongoDB."""
    db = get_db()
    if db is None:
        return []
    try:
        cursor = db.chat_memory.find(
            {"session_id": session_id},
            {"_id": 0, "role": 1, "content": 1}
        ).sort("timestamp", ASCENDING).limit(limit * 2)
        docs = await cursor.to_list(length=None)
        return [{"role": d["role"], "content": d["content"]} for d in docs]
    except Exception as e:
        logger.debug(f"get_chat_history: {e}")
        return []


async def append_chat_memory(session_id: str, role: str, content: str):
    """Append a single message turn to MongoDB chat memory."""
    db = get_db()
    if db is None:
        return
    try:
        await db.chat_memory.insert_one({
            "session_id": session_id,
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow(),
        })
        # Trim old messages beyond MAX_MEMORY_TURNS*2
        count = await db.chat_memory.count_documents({"session_id": session_id})
        if count > MAX_MEMORY_TURNS * 2:
            oldest = await db.chat_memory.find(
                {"session_id": session_id},
                {"_id": 1}
            ).sort("timestamp", ASCENDING).limit(count - MAX_MEMORY_TURNS * 2).to_list(length=None)
            ids = [d["_id"] for d in oldest]
            if ids:
                await db.chat_memory.delete_many({"_id": {"$in": ids}})
    except Exception as e:
        logger.debug(f"append_chat_memory: {e}")


async def clear_chat_memory(session_id: str):
    """Clear all memory for a session."""
    db = get_db()
    if db is None:
        return
    try:
        await db.chat_memory.delete_many({"session_id": session_id})
    except Exception as e:
        logger.debug(f"clear_chat_memory: {e}")


# ─────────────────────────────────────────────────────────────────────────── #
# Auth — Users                                                                  #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_user_by_email(email: str) -> Optional[dict]:
    db = get_db()
    if db is None:
        return None
    try:
        doc = await db.users.find_one({"email": email}, {"_id": 0})
        return doc
    except Exception:
        return None


async def get_user_by_id(user_id: str) -> Optional[dict]:
    db = get_db()
    if db is None:
        return None
    try:
        from bson import ObjectId
        doc = await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0})
        if doc:
            doc["id"] = str(doc.pop("_id"))
        return doc
    except Exception:
        return None


async def create_user(email: str, name: str, password_hash: Optional[str] = None,
                      provider: str = "email", provider_id: Optional[str] = None) -> Optional[dict]:
    db = get_db()
    if db is None:
        return None
    try:
        doc = {
            "email": email,
            "name": name,
            "password_hash": password_hash,
            "provider": provider,
            "provider_id": provider_id,
            "created_at": datetime.utcnow(),
            "last_login": datetime.utcnow(),
        }
        result = await db.users.insert_one(doc)
        doc["id"] = str(result.inserted_id)
        doc.pop("_id", None)
        doc.pop("password_hash", None)
        return doc
    except Exception as e:
        logger.warning(f"create_user: {e}")
        return None


async def update_user_login(email: str):
    db = get_db()
    if db is None:
        return
    try:
        await db.users.update_one(
            {"email": email},
            {"$set": {"last_login": datetime.utcnow()}}
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────── #
# Rate Limiting                                                                 #
# ─────────────────────────────────────────────────────────────────────────── #
async def check_rate_limit(key: str, max_requests: int = 30, window_seconds: int = 60) -> bool:
    """Returns True if request is allowed, False if rate limited."""
    db = get_db()
    if db is None:
        return True  # Allow if no DB
    try:
        now = datetime.utcnow()
        reset_at = now + timedelta(seconds=window_seconds)
        doc = await db.rate_limits.find_one({"key": key})
        if not doc:
            await db.rate_limits.insert_one({
                "key": key, "count": 1, "reset_at": reset_at
            })
            return True
        if doc["count"] >= max_requests:
            return False
        await db.rate_limits.update_one(
            {"key": key},
            {"$inc": {"count": 1}}
        )
        return True
    except Exception:
        return True  # Fail open


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
# Total counts for analytics                                                    #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_counts() -> dict:
    db = get_db()
    if db is None:
        return {"total_queries": 0, "total_recommendations": 0, "total_feedback": 0, "total_users": 0}
    try:
        return {
            "total_queries":         await db.logs.count_documents({}),
            "total_recommendations": await db.recommendations.count_documents({}),
            "total_feedback":        await db.feedback.count_documents({}),
            "total_users":           await db.users.count_documents({}),
        }
    except Exception:
        return {"total_queries": 0, "total_recommendations": 0, "total_feedback": 0, "total_users": 0}
