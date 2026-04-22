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
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, List, Optional

from app.db.mongo_utils import build_mongo_client_kwargs, resolve_mongo_uri

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

MAX_MEMORY_TURNS = 20                                                     
MEMORY_SCAN_LIMIT = 200
_MEMORY_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
}


def get_db():
    """Return Motor chat database handle, or None if unavailable."""
    global _client, _db
    if _db is not None:
        return _db
    if not _MOTOR_OK:
        return None
                                                        
    uri = resolve_mongo_uri("MONGODB_CHAT_URI")
    if not uri:
        return None
    try:
        _client = motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            **build_mongo_client_kwargs(uri),
        )
        db_name = os.getenv("MONGODB_CHAT_DB", "railman_chat")
        _db = _client[db_name]
        logger.info(f"Chat DB: client created → {db_name}")
        return _db
    except Exception as e:
        logger.error(f"Chat DB client creation failed: {e}")
        return None


                                                                               
                                                                                 
                                                                               
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

                             
        await db.chat_memory.create_index("session_id")
        await db.chat_memory.create_index("conversation_id")
        await db.chat_memory.create_index("user_id")
        await db.chat_memory.create_index("sender_type")
        await _repair_chat_memory_message_ids(db)
        await _ensure_chat_memory_message_id_index(db)
        await db.chat_memory.create_index([("session_id", ASCENDING), ("timestamp", DESCENDING)])
        await db.chat_memory.create_index([("conversation_id", ASCENDING), ("timestamp", DESCENDING)])
        await db.chat_memory.create_index("timestamp", expireAfterSeconds=86400 * 30)              
        try:
            await db.chat_memory.create_index(
                [("content", "text"), ("context_tags", "text")],
                name="chat_memory_text_search",
            )
        except Exception as e:
            logger.debug(f"chat_memory text index skipped: {e}")

                              
        await db.users.create_index("email", unique=True)
        await db.users.create_index("google_id", sparse=True)
        await db.users.create_index("github_id", sparse=True)
        await db.users.create_index("created_at")

                             
        await db.rate_limits.create_index("key", unique=True)
        await db.rate_limits.create_index("reset_at", expireAfterSeconds=0)

        logger.info("Chat DB: indexes ensured")
    except Exception as e:
        logger.warning(f"Chat DB: index warning — {e}")


async def _repair_chat_memory_message_ids(db):
    """
    Backfill legacy chat_memory documents so unique indexing on message_id
    does not fail on older rows with null/missing values or accidental duplicates.
    """
    try:
        missing_cursor = db.chat_memory.find(
            {
                "$or": [
                    {"message_id": {"$exists": False}},
                    {"message_id": None},
                    {"message_id": ""},
                ]
            },
            {"_id": 1},
        )
        missing_docs = await missing_cursor.to_list(length=None)
        if missing_docs:
            for doc in missing_docs:
                await db.chat_memory.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"message_id": str(uuid.uuid4())}},
                )
            logger.info("Chat DB: repaired %d legacy chat_memory rows missing message_id", len(missing_docs))
    except Exception as e:
        logger.debug(f"repair missing message_id skipped: {e}")

    try:
        duplicates = await db.chat_memory.aggregate(
            [
                {
                    "$match": {
                        "message_id": {
                            "$exists": True,
                            "$ne": None,
                            "$type": "string",
                        }
                    }
                },
                {
                    "$group": {
                        "_id": "$message_id",
                        "ids": {"$push": "$_id"},
                        "count": {"$sum": 1},
                    }
                },
                {"$match": {"count": {"$gt": 1}}},
            ]
        ).to_list(length=None)

        repaired = 0
        for group in duplicates:
            for doc_id in group["ids"][1:]:
                await db.chat_memory.update_one(
                    {"_id": doc_id},
                    {"$set": {"message_id": str(uuid.uuid4())}},
                )
                repaired += 1
        if repaired:
            logger.info("Chat DB: repaired %d duplicate chat_memory message_id values", repaired)
    except Exception as e:
        logger.debug(f"repair duplicate message_id skipped: {e}")


async def _ensure_chat_memory_message_id_index(db):
    """
    Ensure a unique index exists for valid string message_id values only.
    This avoids startup failures from old legacy rows that still had nulls.
    """
    desired_name = "chat_memory_message_id_unique"
    desired_partial = {"message_id": {"$type": "string"}}

    try:
        indexes = await db.chat_memory.list_indexes().to_list(length=None)
        for index in indexes:
            key = list(index.get("key", {}).items())
            if key != [("message_id", 1)]:
                continue
            current_name = index.get("name")
            current_partial = index.get("partialFilterExpression")
            current_unique = bool(index.get("unique"))
            if current_name == desired_name and current_unique and current_partial == desired_partial:
                return
            await db.chat_memory.drop_index(current_name)
            logger.info("Chat DB: dropped incompatible chat_memory index %s", current_name)
    except Exception as e:
        logger.debug(f"chat_memory message_id index inspection skipped: {e}")

    await db.chat_memory.create_index(
        "message_id",
        name=desired_name,
        unique=True,
        partialFilterExpression=desired_partial,
    )


                                                                               
                                                                                 
                                                                               
def _memory_scope(session_id: str, conversation_id: Optional[str] = None) -> dict:
    return {"conversation_id": conversation_id} if conversation_id else {"session_id": session_id}


def _estimate_token_count(content: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", content or ""))


def _keyword_tokens(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [token for token in tokens if len(token) > 2 and token not in _MEMORY_STOPWORDS]


async def get_chat_history(
    session_id: str,
    limit: int = MAX_MEMORY_TURNS,
    conversation_id: Optional[str] = None,
    include_metadata: bool = False,
) -> List[dict]:
    """Retrieve recent conversation history for a session or conversation."""
    db = get_db()
    if db is None:
        return []
    try:
        limit = max(1, limit)
        projection = {
            "_id": 0,
            "message_id": 1,
            "conversation_id": 1,
            "user_id": 1,
            "role": 1,
            "sender_type": 1,
            "content": 1,
            "timestamp": 1,
            "entities": 1,
            "context_tags": 1,
            "metadata": 1,
            "token_count": 1,
        }
        cursor = db.chat_memory.find(
            _memory_scope(session_id, conversation_id),
            projection,
        ).sort("timestamp", DESCENDING).limit(limit)
        docs = list(reversed(await cursor.to_list(length=None)))
        if include_metadata:
            return docs
        return [
            {
                "role": d.get("role", d.get("sender_type", "user")),
                "content": d["content"],
            }
            for d in docs
        ]
    except Exception as e:
        logger.debug(f"get_chat_history: {e}")
        return []


async def get_relevant_memories(
    session_id: str,
    query: str,
    limit: int = 4,
    conversation_id: Optional[str] = None,
) -> List[dict]:
    """Retrieve semantically relevant prior turns for the current query."""
    db = get_db()
    if db is None or not query.strip():
        return []

    scope = _memory_scope(session_id, conversation_id)
    projection = {
        "_id": 0,
        "message_id": 1,
        "conversation_id": 1,
        "user_id": 1,
        "role": 1,
        "sender_type": 1,
        "content": 1,
        "timestamp": 1,
        "entities": 1,
        "context_tags": 1,
        "metadata": 1,
    }

    try:
        cursor = db.chat_memory.find(
            {**scope, "$text": {"$search": query}},
            {**projection, "score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(limit)
        docs = await cursor.to_list(length=None)
        if docs:
            return docs
    except Exception as e:
        logger.debug(f"get_relevant_memories text search fallback: {e}")

    try:
        cursor = db.chat_memory.find(scope, projection).sort("timestamp", DESCENDING).limit(MEMORY_SCAN_LIMIT)
        docs = await cursor.to_list(length=None)
        query_tokens = set(_keyword_tokens(query))
        if not query_tokens:
            return []

        scored = []
        for doc in docs:
            haystack = " ".join(
                [
                    doc.get("content", ""),
                    " ".join(doc.get("context_tags", [])),
                    " ".join(_keyword_tokens(str(doc.get("entities", {})))),
                ]
            ).lower()
            tokens = set(_keyword_tokens(haystack))
            overlap = len(query_tokens & tokens)
            if overlap <= 0:
                continue
            scored.append((overlap, doc))

        scored.sort(key=lambda item: (-item[0], item[1].get("timestamp", datetime.min)))
        return [doc for _, doc in scored[:limit]]
    except Exception as e:
        logger.debug(f"get_relevant_memories: {e}")
        return []


async def list_conversation_messages(
    session_id: str,
    conversation_id: Optional[str] = None,
    limit: int = 100,
) -> List[dict]:
    return await get_chat_history(
        session_id=session_id,
        conversation_id=conversation_id,
        limit=max(1, limit),
        include_metadata=True,
    )


async def append_chat_memory(
    session_id: str,
    role: str,
    content: str,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    entities: Optional[dict] = None,
    context_tags: Optional[List[str]] = None,
    metadata: Optional[dict] = None,
    sender_type: Optional[str] = None,
):
    """Append a single message turn to MongoDB chat memory."""
    db = get_db()
    if db is None:
        return
    try:
        conversation_key = conversation_id or session_id
        sender = sender_type or role
        timestamp = datetime.utcnow()
        await db.chat_memory.insert_one(
            {
                "message_id": str(uuid.uuid4()),
                "session_id": session_id,
                "conversation_id": conversation_key,
                "user_id": user_id or session_id,
                "sender_type": sender,
                "role": role,
                "content": content,
                "entities": entities or {},
                "context_tags": context_tags or [],
                "metadata": metadata or {},
                "token_count": _estimate_token_count(content),
                "timestamp": timestamp,
            }
        )

        count = await db.chat_memory.count_documents({"conversation_id": conversation_key})
        if count > MAX_MEMORY_TURNS * 2:
            oldest = await db.chat_memory.find(
                {"conversation_id": conversation_key},
                {"_id": 1},
            ).sort("timestamp", ASCENDING).limit(count - MAX_MEMORY_TURNS * 2).to_list(length=None)
            ids = [doc["_id"] for doc in oldest]
            if ids:
                await db.chat_memory.delete_many({"_id": {"$in": ids}})
    except Exception as e:
        logger.debug(f"append_chat_memory: {e}")


async def clear_chat_memory(session_id: str, conversation_id: Optional[str] = None):
    """Clear all memory for a session or specific conversation."""
    db = get_db()
    if db is None:
        return
    try:
        await db.chat_memory.delete_many(_memory_scope(session_id, conversation_id))
    except Exception as e:
        logger.debug(f"clear_chat_memory: {e}")


                                                                               
                                                                                 
                                                                               
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


                                                                               
                                                                                 
                                                                               
async def check_rate_limit(key: str, max_requests: int = 30, window_seconds: int = 60) -> bool:
    """Returns True if request is allowed, False if rate limited."""
    db = get_db()
    if db is None:
        return True                  
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
        return True             


                                                                               
                                                                                 
                                                                               
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
