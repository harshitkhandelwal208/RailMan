"""
Live Train Simulator — MongoDB-backed
======================================
- Loads active trains from MongoDB (falls back to JSON)
- Determines which trains are "on the line" right now based on departure time
- Simulates up to 30 concurrent trains at once (realistic for peak hours)
- Writes live positions back to MongoDB (TTL-indexed collection)
- In-memory state dict for smooth interpolation between API polls
"""
import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from app.services.crowd_engine import predict_crowd

logger = logging.getLogger(__name__)

_DATA = Path(__file__).parent.parent / "data"
MAX_ACTIVE = 30          # max simultaneous trains rendered
PROGRESS_PER_SEC = 0.035 # ~28 s per station segment (realistic feel)

# ─────────────────────────────────────────────────────────────────────────── #
# Static station data (always from file — tiny, no reason to hit DB)           #
# ─────────────────────────────────────────────────────────────────────────── #
with open(_DATA / "stations.json") as f:
    STATIONS: List[dict] = json.load(f)
N = len(STATIONS)

# ─────────────────────────────────────────────────────────────────────────── #
# In-memory simulation state: {train_id: state_dict}                           #
# ─────────────────────────────────────────────────────────────────────────── #
_sim: Dict[str, dict] = {}
_all_trains: List[dict] = []     # full catalogue, refreshed periodically
_last_catalogue_load: float = 0  # unix timestamp


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _load_trains_sync() -> List[dict]:
    """Synchronous fallback — read from JSON."""
    with open(_DATA / "trains.json") as f:
        return json.load(f)


def _active_for_hour(trains: List[dict], hour: int) -> List[dict]:
    """
    Return trains that would realistically be on the line right now.
    A train departing at HH:MM takes roughly (27 × 3.5) = ~95 min end-to-end.
    We include trains that departed within the last 100 minutes.
    """
    now_min = hour * 60 + datetime.now().minute
    eligible = []
    for t in trains:
        depart_min = t["departs_hour"] * 60 + t["departs_minute"]
        # wrap midnight
        diff = (now_min - depart_min) % (24 * 60)
        if 0 <= diff <= 100:          # departed 0–100 min ago
            eligible.append(t)
    # Limit to MAX_ACTIVE, preferring fast trains first
    eligible.sort(key=lambda x: {"fast": 0, "semi": 1, "slow": 2}.get(x["type"], 3))
    return eligible[:MAX_ACTIVE]


def _init_train_state(tmpl: dict, hour: int) -> dict:
    """
    Calculate the train's current position on the line based on
    how long ago it departed relative to now.
    """
    now_min   = hour * 60 + datetime.now().minute
    dep_min   = tmpl["departs_hour"] * 60 + tmpl["departs_minute"]
    elapsed   = (now_min - dep_min) % (24 * 60)  # minutes since departure

    stops = tmpl["stop_indices"]
    mins_per_hop = tmpl.get("mins_per_hop", 3.5)

    # Which segment are we on?
    hops_done = elapsed / mins_per_hop
    seg_idx   = int(hops_done)
    progress  = hops_done - seg_idx   # 0.0 – 1.0 within current segment

    # Clamp to valid stop range
    if seg_idx >= len(stops) - 1:
        seg_idx  = len(stops) - 2
        progress = random.uniform(0.7, 1.0)

    cur_stop_idx  = stops[seg_idx]
    next_stop_idx = stops[min(seg_idx + 1, len(stops) - 1)]

    return {
        "id":          tmpl["id"],
        "name":        tmpl["name"],
        "type":        tmpl["type"],
        "color":       tmpl["color"],
        "direction":   tmpl["direction"],
        "stops":       stops,
        "seg_idx":     seg_idx,
        "progress":    progress,
        "last_tick":   time.time(),
        "mins_per_hop": mins_per_hop,
        "departs_hour": tmpl["departs_hour"],
        "departs_min":  tmpl["departs_minute"],
    }


def _step_train(state: dict) -> None:
    """Advance one train's physics by elapsed wall-clock time."""
    now     = time.time()
    elapsed = now - state["last_tick"]
    state["last_tick"] = now

    state["progress"] += PROGRESS_PER_SEC * elapsed

    if state["progress"] >= 1.0:
        state["progress"] = 0.0
        state["seg_idx"] += 1

        stops = state["stops"]
        if state["seg_idx"] >= len(stops) - 1:
            # Train reached terminus — reverse or reset
            state["seg_idx"]  = 0
            state["progress"] = 0.0
            state["direction"] *= -1
            # Flip stops for return journey
            state["stops"] = list(reversed(stops))


def _train_to_output(state: dict, now: datetime) -> dict:
    """Convert simulation state → API-serialisable dict."""
    stops    = state["stops"]
    seg      = min(state["seg_idx"], len(stops) - 2)
    cur_idx  = stops[seg]
    next_idx = stops[seg + 1]

    cur_st  = STATIONS[cur_idx]
    next_st = STATIONS[next_idx]

    lat = _lerp(cur_st["lat"], next_st["lat"], state["progress"])
    lng = _lerp(cur_st["lng"], next_st["lng"], state["progress"])

    is_weekend = now.weekday() >= 5
    crowd_label, crowd_color, crowd_score = predict_crowd(
        now.hour, now.minute,
        zone=cur_st["zone"],
        train_type=state["type"],
        is_weekend=is_weekend,
    )

    return {
        "id":              state["id"],
        "name":            state["name"],
        "type":            state["type"],
        "color":           state["color"],
        "lat":             round(lat, 6),
        "lng":             round(lng, 6),
        "current_station": cur_st["name"],
        "next_station":    next_st["name"],
        "direction":       "Virar ↑" if state["direction"] == 1 else "Churchgate ↓",
        "crowd":           crowd_label,
        "crowd_color":     crowd_color,
        "crowd_score":     crowd_score,
        "progress":        round(state["progress"], 3),
        "departs":         f"{state['departs_hour']:02d}:{state['departs_min']:02d}",
    }


# ─────────────────────────────────────────────────────────────────────────── #
# Public API (sync — called from FastAPI route)                                 #
# ─────────────────────────────────────────────────────────────────────────── #
def get_trains_sync() -> List[dict]:
    """
    Synchronous version used by the GET /live_trains endpoint.
    Uses the in-memory catalogue; refreshes from file every 5 min.
    """
    global _all_trains, _last_catalogue_load
    now = datetime.now()

    # Reload catalogue every 5 minutes
    if time.time() - _last_catalogue_load > 300 or not _all_trains:
        try:
            _all_trains = _load_trains_sync()
            _last_catalogue_load = time.time()
        except Exception as e:
            logger.warning(f"Failed to load trains: {e}")

    active_templates = _active_for_hour(_all_trains, now.hour)

    # Initialise new trains, remove departed ones
    active_ids = {t["id"] for t in active_templates}
    for tid in list(_sim.keys()):
        if tid not in active_ids:
            del _sim[tid]

    for tmpl in active_templates:
        if tmpl["id"] not in _sim:
            _sim[tmpl["id"]] = _init_train_state(tmpl, now.hour)

    # Step all active trains
    for state in _sim.values():
        _step_train(state)

    return [_train_to_output(s, now) for s in _sim.values()]


# ─────────────────────────────────────────────────────────────────────────── #
# Async version — writes positions to MongoDB after computing                  #
# ─────────────────────────────────────────────────────────────────────────── #
async def get_trains() -> List[dict]:
    """
    Async version: computes positions then upserts to MongoDB.
    Called by the API route.
    """
    results = get_trains_sync()

    # Write to MongoDB (non-blocking, best-effort)
    try:
        from app.db.mongo import upsert_live_position
        for r in results:
            await upsert_live_position(r["id"], r)
    except Exception as e:
        logger.debug(f"live position upsert skipped: {e}")

    return results
