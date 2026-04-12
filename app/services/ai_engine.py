"""
AI Engine — Chat + Intent Handling (v2)
-----------------------------------------
Priority: Anthropic Claude → OpenAI → Rule-based (offline, no key needed)
Context memory: MongoDB-backed persistent memory per session
  - Falls back to in-process dict when MongoDB is unavailable
  - Stores last MAX_HISTORY turns in MongoDB for cross-session continuity
"""
import logging
import os
from datetime import datetime
from typing import List, Optional

from app.services.recommendation_engine import extract_entities, recommend_sync
from app.services.crowd_engine import predict_crowd

logger = logging.getLogger(__name__)

# Fallback in-memory sessions (used when MongoDB unavailable)
_sessions_fallback: dict = {}
MAX_HISTORY = 20  # Increased from 6 — MongoDB can handle more history

SYSTEM_PROMPT = """You are RailMan AI — a premium Mumbai Western Railway assistant.
Personality: knowledgeable, warm, concise, emojis used tastefully.

You help with:
- Best train recommendations (source → destination, time, preference)
- Crowd predictions (peak: 8-11 AM and 6-9 PM = Extreme/High)
- Journey times, station info, platform tips
- Western Line: 28 stations from Churchgate → Virar
- The full timetable has 388 trains daily
- Train types: Slow (stops all stations), Semi-Fast (skips some), Fast (major stops only)
- Fast trains run Churchgate↔Virar or Churchgate↔Borivali only
- Journey time Churchgate to Virar: ~70-80 min (fast), ~110 min (slow)

Mumbai Western Line stations (south to north):
Churchgate, Marine Lines, Charni Road, Grant Road, Mumbai Central, Mahalaxmi, 
Lower Parel, Prabhadevi (Elphinstone Road), Dadar, Matunga Road, Mahim, 
Bandra, Khar Road, Santacruz, Vile Parle, Andheri, Jogeshwari, Goregaon, 
Malad, Kandivali, Borivali, Dahisar, Mira Road, Bhayandar, Naigaon, 
Vasai Road, Nallasopara, Virar

Response style:
- Use **bold** for key info, bullet points for lists
- Crowd emoji: 🟢 Low | 🟡 Medium | 🟠 High | 🔴 Extreme
- Under 200 words unless detail is required
- Always mention current crowd level when asked about trains
- Remember context from earlier in conversation
- Use train's actual display name with number (e.g., #0123 Virar Fast ↑)

Current context: {time_context}
"""


def _get_client():
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if key:
        try:
            import anthropic
            return "anthropic", anthropic.Anthropic(api_key=key)
        except ImportError:
            pass

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if key:
        try:
            from openai import OpenAI
            return "openai", OpenAI(api_key=key)
        except ImportError:
            pass

    return None, None


def _rule_based(message: str, entities: dict) -> str:
    src  = entities.get("source")
    dst  = entities.get("destination")
    pref = entities.get("preference", "balanced")
    t    = entities.get("time")

    if src and dst:
        rec = recommend_sync(src, dst, t, pref)
        if "error" in rec:
            return f"⚠️ {rec['error']}"
        best = rec["best"]
        e = {"Low": "🟢", "Medium": "🟡", "High": "🟠", "Extreme": "🔴"}.get(best["crowd"], "⚪")
        lines = [
            f"🚆 **{best['name']}** (departs {best['departs']})",
            f"",
            f"⏱ ~{best['travel_minutes']} min  {e} {best['crowd']} crowd  🎫 {best['type'].title()}",
        ]
        if rec["alternatives"]:
            lines.append("\n**Alternatives:**")
            for a in rec["alternatives"]:
                e2 = {"Low": "🟢", "Medium": "🟡", "High": "🟠", "Extreme": "🔴"}.get(a["crowd"], "⚪")
                lines.append(f"  • {a['name']} ({a['departs']}) — {e2} {a['crowd']} · {a['travel_minutes']} min")
        lines.append(f"\n📊 Evaluated {rec['meta'].get('trains_evaluated', 0)} trains from the full timetable.")
        return "\n".join(lines)

    now = datetime.now()
    label, _, _ = predict_crowd(now.hour, now.minute)
    e = {"Low": "🟢", "Medium": "🟡", "High": "🟠", "Extreme": "🔴"}.get(label, "⚪")
    return (
        f"👋 I'm **RailMan AI** — your Western Line companion!\n\n"
        f"Right now: {e} **{label}** crowd levels.\n\n"
        f"Try asking:\n"
        f"• *Best train from Borivali to Churchgate at 9 AM*\n"
        f"• *Least crowded Andheri to Dadar right now*\n"
        f"• *How long from Bandra to Virar?*"
    )


async def _get_history_from_db(session_id: str) -> List[dict]:
    """Load chat history from MongoDB, fall back to in-memory."""
    try:
        from app.db.chat_db import get_chat_history
        db_history = await get_chat_history(session_id, limit=MAX_HISTORY)
        if db_history:
            return db_history
    except Exception as e:
        logger.debug(f"DB history load failed: {e}")
    # Fallback to in-memory
    return _sessions_fallback.get(session_id, [])


async def _save_turn_to_db(session_id: str, role: str, content: str):
    """Persist a message turn to MongoDB."""
    try:
        from app.db.chat_db import append_chat_memory
        await append_chat_memory(session_id, role, content)
    except Exception as e:
        logger.debug(f"DB memory save failed: {e}")
    # Always update fallback too
    if session_id not in _sessions_fallback:
        _sessions_fallback[session_id] = []
    _sessions_fallback[session_id].append({"role": role, "content": content})
    # Trim fallback
    _sessions_fallback[session_id] = _sessions_fallback[session_id][-(MAX_HISTORY * 2):]


async def handle_query(
    message: str,
    session_id: Optional[str] = None,
    context: Optional[List[dict]] = None,
) -> dict:
    now = datetime.now()
    entities = extract_entities(message)

    sid = session_id or "default"

    # Load history from MongoDB (with fallback)
    history = await _get_history_from_db(sid)

    # If caller supplied context and we have no history yet, seed from it
    if context and not history:
        for turn in context[-MAX_HISTORY:]:
            history.append(turn)

    # Add user message
    history.append({"role": "user", "content": message})

    system = SYSTEM_PROMPT.format(
        time_context=now.strftime("%A %d %b %Y, %I:%M %p")
    )

    provider, client = _get_client()
    ai_response = None

    if provider == "anthropic" and client:
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=512,
                system=system,
                messages=history[-MAX_HISTORY:],
            )
            ai_response = resp.content[0].text
        except Exception as e:
            logger.error(f"Anthropic error: {e}")

    elif provider == "openai" and client:
        try:
            msgs = [{"role": "system", "content": system}] + history[-MAX_HISTORY:]
            resp = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=512, messages=msgs
            )
            ai_response = resp.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI error: {e}")

    if not ai_response:
        ai_response = _rule_based(message, entities)

    # Persist both turns to DB
    await _save_turn_to_db(sid, "user", message)
    await _save_turn_to_db(sid, "assistant", ai_response)

    # Async recommendation
    recommendation = None
    if entities.get("source") and entities.get("destination"):
        try:
            rec = await recommend(
                entities["source"], entities["destination"],
                entities.get("time"), entities.get("preference", "balanced"),
            )
            if "error" not in rec:
                recommendation = rec
        except Exception:
            pass

    crowd_label, crowd_color, crowd_score = predict_crowd(now.hour, now.minute)
    return {
        "response":       ai_response,
        "meta": {
            "entities":    entities,
            "crowd":       crowd_label,
            "crowd_color": crowd_color,
            "crowd_score": crowd_score,
            "session_id":  sid,
            "timestamp":   now.isoformat(),
            "history_len": len(history),
        },
        "recommendation": recommendation,
    }


# lazy import to avoid circular at module load
from app.services.recommendation_engine import recommend
