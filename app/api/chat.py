"""
POST /api/chat           — AI conversation endpoint
POST /api/recommend      — Structured recommendation endpoint
POST /api/feedback       — User feedback collection
POST /api/clear_memory   — Clear session chat memory
GET  /api/chat/history/{session_id}  — Retrieve stored messages for a session
GET  /api/chat/status    — LLM runtime + knowledge base status
"""
import uuid
from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional

from app.models.schemas import (
    ChatRequest, RecommendRequest, FeedbackRequest, ClearMemoryRequest,
)
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
                detail="Rate limit exceeded. Please wait a moment before sending more messages.",
            )
    except HTTPException:
        raise
    except Exception:
        pass                               


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    await _rate_limit_chat(request)
    sid = req.session_id or str(uuid.uuid4())
    result = await handle_query(
        req.message,
        sid,
        conversation_id=req.conversation_id,
        user_id=req.user_id,
        context=req.context,
    )
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
        await clear_chat_memory(req.session_id, req.conversation_id)
    except Exception:
        pass
    return {"status": "ok", "message": "Conversation memory cleared."}


@router.get("/chat/history/{session_id}")
async def get_history(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    conversation_id: Optional[str] = Query(default=None),
    include_metadata: bool = Query(default=False),
):
    """
    Retrieve stored chat messages for a session.

    - **session_id**: The session UUID used when chatting.
    - **limit**: Max messages to return (1–200, default 50).
    - **include_metadata**: When true, includes entities, context_tags, token_count.
    """
    try:
        from app.db.chat_db import list_conversation_messages
        messages = await list_conversation_messages(
            session_id=session_id,
            conversation_id=conversation_id,
            limit=limit,
        )
        if not include_metadata:
                                                               
            messages = [
                {
                    "role":      m.get("role", m.get("sender_type", "user")),
                    "content":   m.get("content", ""),
                    "timestamp": m.get("timestamp"),
                }
                for m in messages
            ]
        return {
            "session_id":      session_id,
            "conversation_id": conversation_id or session_id,
            "count":           len(messages),
            "messages":        messages,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"History retrieval failed: {exc}")


@router.get("/chat/status")
async def chat_status():
    """
    Return LLM runtime configuration and knowledge base stats.
    Useful for debugging offline/online provider state.
    """
    from app.services.llm_runtime import get_runtime_status
    from app.services.knowledge_base import get_knowledge_stats

    runtime = get_runtime_status()
    kb_stats = get_knowledge_stats()

    return {
        "llm_runtime":    runtime,
        "knowledge_base": kb_stats,
    }
