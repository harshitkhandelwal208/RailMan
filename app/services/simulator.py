"""
Live Train Simulator — MongoDB-backed (v2)
==========================================
- Loads active trains from MongoDB (falls back to JSON)
- Determines which trains are "on the line" right now based on real departure time
- Simulates up to 30 concurrent trains at once
- Writes live positions back to MongoDB (TTL-indexed collection)
- In-memory state dict for smooth interpolation between API polls

Speed fix: PROGRESS_PER_SEC is now derived from each train's mins_per_hop
  so trains move at realistic real-time speed (~3-4 min per station hop).
  A 4-min/hop train advances 1/(4×60) = 0.00417 progress units per second.
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from app.services.crowd_engine import predict_crowd

logger = logging.getLogger(__name__)

_DATA = Path(__file__).parent.parent / "data"
MAX_ACTIVE = 30

# ── Station data ─────────────────────────────────────────────────────────── #
with open(_DATA / "stations.json") as f:
    STATIONS: List[dict] = json.load(f)
N = len(STATIONS)

# ── In-memory simulation state ───────────────────────────────────────────── #
_sim: Dict[str, dict] = {}
_all_trains: List[dict] = []
_last_catalogue_load: float = 0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _load_trains_sync() -> List[dict]:
    with open(_DATA / "trains.json") as f:
        return json.load(f)


def _active_for_time(trains: List[dict], now_minutes: int) -> List[dict]:
    """
    Return trains realistically on the line right now.
    A train is active if it departed within the last (num_stops × mins_per_hop) minutes.
    Uses the actual end-to-end journey time per train type.
    """
    eligible = []
    for t in trains:
        dep_min = t["departs_hour"] * 60 + t["departs_minute"]
        num_hops = max(1, len(t.get("stop_indices", [])) - 1)
        mph = t.get("mins_per_hop", 4.0)
        journey_min = num_hops * mph

        # Minutes elapsed since departure (wrap midnight)
        diff = (now_minutes - dep_min) % (24 * 60)
        if 0 <= diff <= journey_min:
            eligible.append((t, diff))

    # Sort fast trains first (they tend to be more visible / important)
    eligible.sort(key=lambda x: {"fast": 0, "semi": 1, "slow": 2}.get(x[0].get("type", "slow"), 3))
    return [t for t, _ in eligible[:MAX_ACTIVE]]


def _init_train_state(tmpl: dict, elapsed_minutes: float) -> dict:
    """
    Calculate the train's current position based on elapsed real time since departure.
    Uses the actual mins_per_hop so position matches the real timetable.
    """
    stops = tmpl["stop_indices"]
    mph = tmpl.get("mins_per_hop", 4.0)

    hops_done = elapsed_minutes / mph
    seg_idx = int(hops_done)
    progress = hops_done - seg_idx  # 0.0–1.0 within segment

    if seg_idx >= len(stops) - 1:
        seg_idx = len(stops) - 2
        progress = 0.95  # near terminus

    return {
        "id":           tmpl["id"],
        "name":         tmpl["name"],
        "display_name": tmpl.get("display_name", tmpl["name"]),
        "type":         tmpl["type"],
        "color":        tmpl["color"],
        "direction":    tmpl["direction"],
        "stops":        list(stops),
        "seg_idx":      max(0, seg_idx),
        "progress":     min(0.999, max(0.0, progress)),
        "last_tick":    time.time(),
        # Real speed: progress advances 1/(mph*60) per second
        "speed":        1.0 / (mph * 60.0),
        "departs_hour": tmpl["departs_hour"],
        "departs_min":  tmpl["departs_minute"],
        # Schedule data for display
        "stop_indices": list(stops),
        "mins_per_hop": mph,
        "number":       tmpl.get("number", 0),
    }


def _step_train(state: dict) -> None:
    """Advance one train's physics by elapsed wall-clock time at realistic speed."""
    now = time.time()
    elapsed = now - state["last_tick"]
    state["last_tick"] = now

    # Advance progress at realistic real-time speed
    state["progress"] += state["speed"] * elapsed

    if state["progress"] >= 1.0:
        state["progress"] = 0.0
        state["seg_idx"] += 1

        stops = state["stops"]
        if state["seg_idx"] >= len(stops) - 1:
            # Train reached terminus — mark as done (will be removed next cycle)
            state["seg_idx"] = len(stops) - 2
            state["progress"] = 0.999
            state["_done"] = True


def _train_to_output(state: dict, now: datetime) -> dict:
    """Convert simulation state → API-serialisable dict."""
    stops = state["stops"]
    seg = min(state["seg_idx"], len(stops) - 2)
    cur_idx = stops[seg]
    next_idx = stops[seg + 1]

    cur_st = STATIONS[cur_idx]
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

    # Compute next station ETA
    progress_left = 1.0 - state["progress"]
    seconds_to_next = progress_left / state["speed"] if state["speed"] > 0 else 0
    eta_mins = round(seconds_to_next / 60, 1)

    # Build scheduled stops list for popup
    stop_names = [STATIONS[i]["name"] for i in state["stop_indices"] if i < len(STATIONS)]

    return {
        "id":              state["id"],
        "name":            state["name"],
        "display_name":    state["display_name"],
        "number":          state["number"],
        "type":            state["type"],
        "color":           state["color"],
        "lat":             round(lat, 6),
        "lng":             round(lng, 6),
        "current_station": cur_st["name"],
        "next_station":    next_st["name"],
        "eta_minutes":     eta_mins,
        "direction":       "↑ Virar" if state["direction"] == 1 else "↓ Churchgate",
        "crowd":           crowd_label,
        "crowd_color":     crowd_color,
        "crowd_score":     crowd_score,
        "progress":        round(state["progress"], 3),
        "departs":         f"{state['departs_hour']:02d}:{state['departs_min']:02d}",
        "stops":           stop_names,
        "total_stops":     len(stop_names),
    }


# ─────────────────────────────────────────────────────────────────────────── #
# Public API                                                                    #
# ─────────────────────────────────────────────────────────────────────────── #
def get_trains_sync() -> List[dict]:
    global _all_trains, _last_catalogue_load
    now = datetime.now()
    now_minutes = now.hour * 60 + now.minute

    # Reload catalogue every 5 minutes
    if time.time() - _last_catalogue_load > 300 or not _all_trains:
        try:
            _all_trains = _load_trains_sync()
            _last_catalogue_load = time.time()
        except Exception as e:
            logger.warning(f"Failed to load trains: {e}")

    active_templates = _active_for_time(_all_trains, now_minutes)
    active_ids = {t["id"] for t in active_templates}

    # Remove trains that finished their journey
    for tid in list(_sim.keys()):
        if tid not in active_ids or _sim[tid].get("_done"):
            del _sim[tid]

    # Initialise new trains with correct position
    for tmpl in active_templates:
        if tmpl["id"] not in _sim:
            dep_min = tmpl["departs_hour"] * 60 + tmpl["departs_minute"]
            elapsed = (now_minutes - dep_min) % (24 * 60)
            _sim[tmpl["id"]] = _init_train_state(tmpl, float(elapsed))

    # Step all active trains
    for state in _sim.values():
        _step_train(state)

    return [_train_to_output(s, now) for s in _sim.values()]


async def get_trains() -> List[dict]:
    results = get_trains_sync()

    try:
        from app.db.trains_db import upsert_live_position
        for r in results:
            await upsert_live_position(r["id"], r)
    except Exception as e:
        logger.debug(f"live position upsert skipped: {e}")

    return results
