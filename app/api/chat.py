"""
POST /api/chat       — AI conversation endpoint
POST /api/recommend  — Structured recommendation endpoint
POST /api/feedback   — User feedback collection
POST /api/clear_memory — Clear session chat memory
"""
import uuid
from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import ChatRequest, RecommendRequest, FeedbackRequest, ClearMemoryRequest
from app.services.ai_engine import handle_query
from app.services.recommendation_engine import recommend
from app.db.chat_db import log_query, log_recommendation, save_feedback

router = APIRouter(prefix="/api", tags=["chat"])


async def _rate_limit_chat(request: Request):
    ip = request.client.host if request.client else "unknown"
    try:
        from app.db.chat_db import check_rate_limit
        allowed = await check_rate_limit(f"chat:{ip}", max_requests=30, window_seconds=60)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please wait a moment before sending more messages."
            )
    except HTTPException:
        raise
    except Exception:
        pass  # Fail open if DB unavailable


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    await _rate_limit_chat(request)
    sid = req.session_id or str(uuid.uuid4())
    result = await handle_query(req.message, sid, req.context)
    try:
        await log_query(sid, req.message, result)
    except Exception:
        pass
    return result


@router.post("/recommend")
async def get_recommendation(req: RecommendRequest):
    result = await recommend(req.source, req.destination, req.time, req.preference)
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    sid = str(uuid.uuid4())
    try:
        await log_recommendation(sid, req.dict(), result)
    except Exception:
        pass
    return result


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    await save_feedback(req.session_id, req.rating, req.comment)
    return {"status": "ok", "message": "Thank you for your feedback! 🙏"}


@router.post("/clear_memory")
async def clear_memory(req: ClearMemoryRequest):
    """Clear all stored conversation history for a session."""
    try:
        from app.db.chat_db import clear_chat_memory
        await clear_chat_memory(req.session_id)
    except Exception:
        pass
    return {"status": "ok", "message": "Conversation memory cleared."}
