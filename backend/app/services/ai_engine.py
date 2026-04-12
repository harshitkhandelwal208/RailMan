"""
AI Engine — Chat + Intent Handling
------------------------------------
Priority: Anthropic Claude → OpenAI → Rule-based (offline, no key needed)
Context memory: last 6 message-pairs per session (in-process dict)
"""
import logging
import os
from datetime import datetime
from typing import List, Optional

from app.services.recommendation_engine import extract_entities, recommend_sync
from app.services.crowd_engine import predict_crowd

logger = logging.getLogger(__name__)

_sessions: dict = {}
MAX_HISTORY = 6

SYSTEM_PROMPT = """You are RailMan AI — a premium Mumbai Western Railway assistant.
Personality: knowledgeable, warm, concise, emojis used tastefully.

You help with:
- Best train recommendations (source → destination, time, preference)
- Crowd predictions (peak: 8-11 AM and 6-9 PM = Extreme/High)
- Journey times, station info, platform tips
- Western Line: 28 stations from Churchgate → Virar
- The full timetable has 388 trains daily

Response style:
- Use **bold** for key info, bullet points for lists
- Crowd emoji: 🟢 Low | 🟡 Medium | 🟠 High | 🔴 Extreme
- Under 200 words unless detail is required
- Always mention current crowd level when asked about trains

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
        e = {"Low":"🟢","Medium":"🟡","High":"🟠","Extreme":"🔴"}.get(best["crowd"],"⚪")
        lines = [
            f"🚆 **{best['name']}** (departs {best['departs']})",
            f"",
            f"⏱ ~{best['travel_minutes']} min  {e} {best['crowd']} crowd  🎫 {best['type'].title()}",
        ]
        if rec["alternatives"]:
            lines.append("\n**Alternatives:**")
            for a in rec["alternatives"]:
                e2 = {"Low":"🟢","Medium":"🟡","High":"🟠","Extreme":"🔴"}.get(a["crowd"],"⚪")
                lines.append(f"  • {a['name']} ({a['departs']}) — {e2} {a['crowd']} · {a['travel_minutes']} min")
        lines.append(f"\n📊 Evaluated {rec['meta'].get('trains_evaluated',0)} trains from the full timetable.")
        return "\n".join(lines)

    now = datetime.now()
    label, _, _ = predict_crowd(now.hour, now.minute)
    e = {"Low":"🟢","Medium":"🟡","High":"🟠","Extreme":"🔴"}.get(label,"⚪")
    return (
        f"👋 I'm **RailMan AI** — your Western Line companion!\n\n"
        f"Right now: {e} **{label}** crowd levels.\n\n"
        f"Try asking:\n"
        f"• *Best train from Borivali to Churchgate at 9 AM*\n"
        f"• *Least crowded Andheri to Dadar right now*\n"
        f"• *How long from Bandra to Virar?*"
    )


async def handle_query(
    message: str,
    session_id: Optional[str] = None,
    context: Optional[List[dict]] = None,
) -> dict:
    now = datetime.now()
    entities = extract_entities(message)

    sid = session_id or "default"
    if sid not in _sessions:
        _sessions[sid] = []
    history = _sessions[sid]
    if context and not history:
        history.extend(context[-MAX_HISTORY:])
    history.append({"role": "user", "content": message})

    system = SYSTEM_PROMPT.format(
        time_context=now.strftime("%A %d %b %Y, %I:%M %p")
    )

    provider, client = _get_client()
    ai_response = None

    if provider == "anthropic" and client:
        try:
            resp = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=512,
                system=system,
                messages=history[-MAX_HISTORY:],
            )
            ai_response = resp.content[0].text
        except Exception as e:
            logger.error(f"Anthropic error: {e}")

    elif provider == "openai" and client:
        try:
            msgs = [{"role":"system","content":system}] + history[-MAX_HISTORY:]
            resp = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=512, messages=msgs
            )
            ai_response = resp.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI error: {e}")

    if not ai_response:
        ai_response = _rule_based(message, entities)

    history.append({"role": "assistant", "content": ai_response})
    _sessions[sid] = history[-(MAX_HISTORY * 2):]

    # Async recommendation (pulls from MongoDB)
    recommendation = None
    if entities.get("source") and entities.get("destination"):
        try:
            rec = await recommend(
                entities["source"], entities["destination"],
                entities.get("time"), entities.get("preference","balanced"),
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
        },
        "recommendation": recommendation,
    }


# lazy import to avoid circular at module load
from app.services.recommendation_engine import recommend
