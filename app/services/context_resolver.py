"""
Context Resolver — Follow-up detection and entity merging.

When a user sends a follow-up message (e.g. "What about 30 minutes later?"
or "How crowded is that route?"), this module:

1. Detects follow-up intent via signal-word patterns and missing entity heuristics.
2. Walks backwards through conversation history to find the most recent
   complete entity set (source, destination, time, preference).
3. Applies time-delta arithmetic for messages like "30 minutes later".
4. Returns a merged entity dict so the rest of the pipeline has full context
   without ever asking the user to repeat themselves.
"""
import re
from typing import List, Optional

                                                                               

_FOLLOWUP_PATTERNS = [
    r"\b(what about|how about|and the|is it|are they|will it|is that|that one|the other|alternatively)\b",
    r"\b(what would you recommend|what do you recommend|would you recommend|which one do you recommend|recommend one|recommend instead|suggest one|what should i take)\b",
    r"\b(instead|as well|too|also)\b",
    r"\b(faster|slower|quicker|less crowded|more comfortable|least crowded)\b",
    r"\b(don'?t mind waiting|can wait|wait a bit|wait longer|rather wait|okay waiting|fine waiting)\b",
    r"\b(an? hour|30 min|half an? hour|minutes?)\s+(later|earlier|sooner|after|before)\b",
    r"\b(how long|how far|travel time|journey time|duration)\b",
    r"\b(crowded|busy|rush|empty|quiet|packed|safe)\b",
    r"\b(platform|track|stop|coach|compartment|gate)\b",
    r"\b(next one|previous|earlier one|later one|after that|before that)\b",
    r"\b(still|same route|that route|this route)\b",
]

_FOLLOWUP_RE = re.compile("|".join(_FOLLOWUP_PATTERNS), re.IGNORECASE)

                                                                               

_TIME_DELTA_RE = re.compile(
    r"(\d+)\s*(min(?:ute)?s?|h(?:our)?s?)\s*(later|earlier|before|after|sooner)",
    re.IGNORECASE,
)

                                                                               

_PREF_FASTEST     = {"fastest", "fast", "quickest", "quick", "express", "speedy"}
_PREF_COMFORT     = {"least crowded", "comfortable", "empty", "quiet", "uncrowded",
                     "less crowded", "more comfortable", "spacious"}
_PREF_BALANCED    = {"balanced", "normal", "any"}
_WAIT_TOLERANCE   = {
    "don't mind waiting",
    "dont mind waiting",
    "can wait",
    "wait a bit",
    "wait longer",
    "rather wait",
    "okay waiting",
    "fine waiting",
}


def _is_followup(message: str, current_entities: dict) -> bool:
    """Return True if the message appears to be a follow-up with no new route info."""
    has_route = bool(current_entities.get("source") and current_entities.get("destination"))
    if has_route:
        return False
    return bool(_FOLLOWUP_RE.search(message))


def _extract_time_delta_minutes(message: str) -> Optional[int]:
    """
    Return a signed minute delta from phrases like '30 minutes later' or '1 hour earlier'.
    Positive = later, negative = earlier.
    """
    m = _TIME_DELTA_RE.search(message)
    if not m:
        return None
    value = int(m.group(1))
    unit  = m.group(2).lower()
    direction = m.group(3).lower()
    minutes = value * 60 if unit.startswith("h") else value
    return -minutes if direction in ("earlier", "before") else minutes


def _apply_time_delta(base_time: Optional[str], delta_minutes: int) -> str:
    """Apply a signed minute delta to a HH:MM string, wrapping at midnight."""
    if not base_time:
        return base_time
    try:
        h, m = map(int, base_time.split(":"))
        total = (h * 60 + m + delta_minutes) % 1440
        return f"{total // 60:02d}:{total % 60:02d}"
    except Exception:
        return base_time


def _override_preference(message: str, current_pref: str) -> str:
    """Check if the user's message implies a preference change."""
    msg = message.lower()
    for kw in _PREF_COMFORT:
        if kw in msg:
            return "least_crowded"
    for kw in _WAIT_TOLERANCE:
        if kw in msg:
            return "least_crowded"
    for kw in _PREF_FASTEST:
        if kw in msg:
            return "fastest"
    return current_pref


def _entities_from_history_turn(turn: dict) -> dict:
    """Extract stored entity dict from a history turn (if present)."""
    return turn.get("entities") or {}


def resolve_entities(
    message: str,
    current_entities: dict,
    history: List[dict],
) -> dict:
    """
    Merge current_entities with context from history for follow-up messages.

    Returns a (possibly enriched) entity dict ready for recommendation engine
    and AI engine. If the message already contains a full route, returns it
    unchanged (no side-effects on the original dict).
    """
    if not _is_followup(message, current_entities):
                                                                    
        pref = _override_preference(message, current_entities.get("preference", "balanced"))
        return {**current_entities, "preference": pref}

    merged: dict = dict(current_entities)

                                                                       
    for turn in reversed(history):
        turn_entities = _entities_from_history_turn(turn)
        for key in ("source", "destination", "time", "preference", "preferred_line", "train_id"):
            if not merged.get(key) and turn_entities.get(key):
                merged[key] = turn_entities[key]
                                                      
        if merged.get("source") and merged.get("destination"):
            break

                                                                       
    delta = _extract_time_delta_minutes(message)
    if delta is not None and merged.get("time"):
        merged["time"] = _apply_time_delta(merged["time"], delta)

                                                                              
    merged["preference"] = _override_preference(
        message, merged.get("preference", "balanced")
    )

    return merged


def is_greeting(message: str) -> bool:
    """Return True if the message is a simple greeting with no query intent."""
    cleaned = message.strip().lower().rstrip("!.,?")
    greetings = {
        "hi", "hello", "hey", "hii", "hiii", "good morning", "good evening",
        "good afternoon", "good night", "sup", "yo", "namaste", "namaskar",
        "hy", "howdy",
    }
    return cleaned in greetings or re.match(r"^(hi+|hey+|hello+)\s*$", cleaned) is not None


def is_help_request(message: str) -> bool:
    """Return True if the user is asking what the bot can do."""
    cleaned = message.strip().lower()
    patterns = [
        r"\b(help|what can you do|capabilities|features|how do you work|what do you know)\b",
        r"\b(instructions?|commands?|options?|guide|tutorial|explain yourself)\b",
    ]
    return any(re.search(p, cleaned) for p in patterns)
