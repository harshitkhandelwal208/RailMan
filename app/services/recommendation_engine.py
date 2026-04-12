"""
Recommendation Engine
---------------------
Scores all trains between source and destination using:
  1. Crowd level (Gaussian model)
  2. Estimated travel time
  3. User preference weight

Now powered by the full 388-train dataset from MongoDB.
Falls back to JSON if DB unavailable.
"""
import json
import os
import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from app.services.crowd_engine import predict_crowd, forecast_day

_DATA = Path(__file__).parent.parent / "data"

# ── Static data loaded at import time (fallback) ──────────────────────────── #
with open(_DATA / "stations.json") as f:
    STATIONS: List[dict] = json.load(f)

STATION_BY_ID   = {s["id"]: s   for s in STATIONS}
STATION_BY_NAME = {s["name"].lower(): s for s in STATIONS}
STATION_IDX     = {s["id"]: s["index"] for s in STATIONS}


# ── Helpers ──────────────────────────────────────────────────────────────── #
def _find_station(q: str) -> Optional[dict]:
    q = q.strip().lower()
    if q.upper() in STATION_BY_ID:
        return STATION_BY_ID[q.upper()]
    if q in STATION_BY_NAME:
        return STATION_BY_NAME[q]
    for name, st in STATION_BY_NAME.items():
        if q in name or name in q:
            return st
    return None


def _train_serves(train: dict, src_idx: int, dst_idx: int) -> bool:
    """Check if a train's stop list covers src → dst in the right direction."""
    stops = train.get("stop_indices", [])
    if not stops:
        return False
    direction = train.get("direction", 1)

    # Train goes Churchgate→Virar (direction=1): src_idx < dst_idx
    if direction == 1 and src_idx >= dst_idx:
        return False
    if direction == -1 and src_idx <= dst_idx:
        return False

    # Both src and dst must appear in stops in the right order
    try:
        si = stops.index(src_idx)
        di = stops.index(dst_idx)
        return si < di if direction == 1 else si < di
    except ValueError:
        # Station not in stops — train does not stop here
        return False


def _estimate_minutes(train: dict, src_idx: int, dst_idx: int) -> int:
    hops = abs(dst_idx - src_idx)
    mph  = train.get("mins_per_hop", {"fast": 3.0, "semi": 3.5, "slow": 4.0}.get(train.get("type","slow"), 3.5))
    return max(3, int(hops * mph))


def _score_train(train, src_idx, dst_idx, hour, minute, preference, zone):
    # Compute actual departure time from src_idx
    dep_h = train.get("departs_hour", 0)
    dep_m = train.get("departs_minute", 0)
    start_idx = train.get("start_index", 0)
    mph = train.get("mins_per_hop", {"fast": 3.0, "semi": 3.5, "slow": 4.0}.get(train.get("type","slow"), 3.5))
    
    hops_from_start = abs(src_idx - start_idx)
    mins_to_reach_src = int(hops_from_start * mph)
    
    arr_total_mins = dep_h * 60 + dep_m + mins_to_reach_src
    src_dep_h = (arr_total_mins // 60) % 24
    src_dep_m = arr_total_mins % 60

    req_total_mins = hour * 60 + minute
    diff = (arr_total_mins - req_total_mins) % (24 * 60)
    
    # Only recommend trains departing in the next 300 minutes (5 hours) to account for midnight dead-zones
    if diff > 300:
        return -1, None

    crowd_label, crowd_color, crowd_score = predict_crowd(
        hour, minute, zone=zone, train_type=train.get("type","slow")
    )
    travel_minutes = _estimate_minutes(train, src_idx, dst_idx)

    crowd_s = 1.0 - crowd_score / 100.0
    time_s  = max(0.0, 1.0 - travel_minutes / 120.0)

    w = {
        "fastest":      (0.20, 0.80),
        "least_crowded":(0.80, 0.20),
        "balanced":     (0.50, 0.50),
    }.get(preference, (0.50, 0.50))

    composite = (w[0] * crowd_s) + (w[1] * time_s)
    
    # Small penalty for trains leaving further in the future
    composite -= (diff / 300.0) * 0.1  

    return composite, {
        "id":             train["id"],
        "name":           train["name"],
        "type":           train.get("type", "slow"),
        "color":          train.get("color", "#6366F1"),
        "crowd":          crowd_label,
        "crowd_color":    crowd_color,
        "crowd_score":    crowd_score,
        "travel_minutes": travel_minutes,
        "departs":        f"{src_dep_h:02d}:{src_dep_m:02d}",
        "composite_score":round(composite, 3),
    }


# ── Public sync version (used by rule-based fallback in ai_engine) ───────── #
def _recommend_with_trains(
    trains, source, destination, time_str=None, preference="balanced"
) -> dict:
    now = datetime.now()
    if time_str:
        try:
            h, m = map(int, time_str.split(":"))
        except Exception:
            h, m = now.hour, now.minute
    else:
        h, m = now.hour, now.minute

    src_st = _find_station(source)
    dst_st = _find_station(destination)

    if not src_st:
        return {"error": f"Station '{source}' not found on Western Line."}
    if not dst_st:
        return {"error": f"Station '{destination}' not found on Western Line."}

    src_idx = src_st["index"]
    dst_idx = dst_st["index"]
    if src_idx == dst_idx:
        return {"error": "Source and destination are the same station."}

    zone = src_st["zone"]
    scored = []
    for train in trains:
        if not _train_serves(train, src_idx, dst_idx):
            continue
        score, meta = _score_train(train, src_idx, dst_idx, h, m, preference, zone)
        if score != -1:
            scored.append((score, meta))

    if not scored:
        return {"error": "No trains found for this route at the requested time."}

    scored.sort(key=lambda x: x[0], reverse=True)
    best         = scored[0][1]
    alternatives = [s[1] for s in scored[1:3]]

    emoji = {"Low":"🟢","Medium":"🟡","High":"🟠","Extreme":"🔴"}.get(best["crowd"],"⚪")
    explanation = (
        f"✅ **{best['name']}** — best option from "
        f"**{src_st['name']}** → **{dst_st['name']}** at **{h:02d}:{m:02d}**\n\n"
        f"⏱ Travel: **{best['travel_minutes']} min**  "
        f"{emoji} Crowd: **{best['crowd']}**  "
        f"🚆 Type: **{best['type'].title()}**\n\n"
    )
    if preference == "fastest":
        explanation += "🏃 Optimised for **fastest journey**."
    elif preference == "least_crowded":
        explanation += "😌 Optimised for **least crowded** experience."
    else:
        explanation += "⚖️ Best **overall balance** of speed and comfort."

    return {
        "best":           best,
        "alternatives":   alternatives,
        "explanation":    explanation,
        "crowd_forecast": forecast_day(zone=zone, train_type=best["type"]),
        "meta": {
            "source":      src_st["name"],
            "destination": dst_st["name"],
            "query_time":  f"{h:02d}:{m:02d}",
            "preference":  preference,
            "trains_evaluated": len(scored),
        },
    }


# ── Public async recommend (loads from MongoDB) ───────────────────────────── #
async def recommend(
    source: str,
    destination: str,
    time_str: Optional[str] = None,
    preference: str = "balanced",
) -> dict:
    """Async version — loads full train catalogue from MongoDB."""
    try:
        from app.db.trains_db import get_all_trains
        trains = await get_all_trains()
    except Exception:
        trains = json.load(open(_DATA / "trains.json"))

    return _recommend_with_trains(trains, source, destination, time_str, preference)


# ── Sync version for backward-compat (used by ai_engine fallback) ─────────── #
def recommend_sync(source, destination, time_str=None, preference="balanced") -> dict:
    trains = json.load(open(_DATA / "trains.json"))
    return _recommend_with_trains(trains, source, destination, time_str, preference)


# ── NLP Entity Extractor ─────────────────────────────────────────────────── #
def extract_entities(text: str) -> dict:
    text_lower = text.lower()

    # Time: "9 AM", "9:30 pm", "21:00"
    time_match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b', text_lower)
    time_str = None
    if time_match:
        hour = int(time_match.group(1))
        mins = int(time_match.group(2) or 0)
        period = time_match.group(3)
        if period == "pm" and hour < 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        time_str = f"{hour:02d}:{mins:02d}"

    preference = "balanced"
    if any(w in text_lower for w in ["fast","quick","earliest","least time","express"]):
        preference = "fastest"
    elif any(w in text_lower for w in ["less crowd","least crowd","empty","comfortable","uncrowded"]):
        preference = "least_crowded"

    found = []
    for st in STATIONS:
        if st["name"].lower() in text_lower:
            pos = text_lower.find(st["name"].lower())
            found.append((pos, st))
    found.sort(key=lambda x: x[0])

    source      = found[0][1]["name"] if len(found) > 0 else None
    destination = found[1][1]["name"] if len(found) > 1 else None

    ft = re.search(r'from\s+(\w[\w\s]*?)\s+to\s+(\w[\w\s]+)', text_lower)
    if ft:
        sc = _find_station(ft.group(1).strip())
        dc = _find_station(ft.group(2).strip())
        if sc: source      = sc["name"]
        if dc: destination = dc["name"]

    return {"source": source, "destination": destination, "time": time_str, "preference": preference}
