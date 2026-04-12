"""
POST /chat  — AI conversation endpoint
POST /recommend — Structured recommendation endpoint
POST /feedback  — User feedback collection
"""
import uuid
from fastapi import APIRouter, HTTPException

from app.models.schemas import ChatRequest, RecommendRequest, FeedbackRequest
from app.services.ai_engine import handle_query
from app.services.recommendation_engine import recommend
from app.db.mongo import log_query, log_recommendation, save_feedback

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
async def chat(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    result = await handle_query(req.message, sid, req.context)
    # Fire-and-forget DB logging (non-blocking)
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
