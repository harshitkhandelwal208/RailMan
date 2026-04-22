"""
Live Train Simulator — multi-line and route-aware.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Dict, List

from app.services.crowd_engine import predict_crowd
from app.services.rail_network import load_stations, load_trains, train_line, train_stop_ids
from app.services.time_utils import get_service_now

logger = logging.getLogger(__name__)

MAX_ACTIVE = 30
STATIONS = load_stations()
STATION_BY_ID = {s["id"]: s for s in STATIONS}
_sim: Dict[str, dict] = {}
_all_trains: List[dict] = []
_last_catalogue_load: float = 0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _load_trains_sync() -> List[dict]:
    return load_trains()


def _active_for_time(trains: List[dict], now_minutes: int) -> List[dict]:
    eligible = []
    for t in trains:
        dep_min = t["departs_hour"] * 60 + t["departs_minute"]
        stops = train_stop_ids(t)
        num_hops = max(1, len(stops) - 1)
        mph = t.get("mins_per_hop", 4.0)
        journey_min = num_hops * mph
        diff = (now_minutes - dep_min) % (24 * 60)
        if 0 <= diff <= journey_min:
            eligible.append((t, diff))
    eligible.sort(key=lambda x: {"fast": 0, "semi": 1, "slow": 2}.get(x[0].get("type", "slow"), 3))
    return [t for t, _ in eligible[:MAX_ACTIVE]]


def _init_train_state(tmpl: dict, elapsed_minutes: float) -> dict:
    stops = train_stop_ids(tmpl)
    if len(stops) < 2:
        return {}
    mph = tmpl.get("mins_per_hop", 4.0)
    num_hops = len(stops) - 1
    total_journey = max(1.0, num_hops * mph)
    progress = min(max(elapsed_minutes / total_journey, 0.0), 0.9999)
    segment_float = progress * num_hops
    seg_idx = min(int(segment_float), num_hops - 1)
    seg_t = segment_float - seg_idx
    a = STATION_BY_ID[stops[seg_idx]]
    b = STATION_BY_ID[stops[seg_idx + 1]]
    return {
        'id': tmpl['id'],
        'template': tmpl,
        'stops': stops,
        'segment_idx': seg_idx,
        'segment_t': seg_t,
        'elapsed': elapsed_minutes,
        'total_journey': total_journey,
        'progress': progress,
        'lat': _lerp(a['lat'], b['lat'], seg_t),
        'lng': _lerp(a['lng'], b['lng'], seg_t),
        'last_tick': time.time(),
    }


def _step_train(state: dict):
    tmpl = state['template']
    mph = tmpl.get('mins_per_hop', 4.0)
    num_hops = max(1, len(state['stops']) - 1)
    total_journey = max(1.0, num_hops * mph)
    now = time.time()
    delta_min = (now - state.get('last_tick', now)) / 60.0
    state['last_tick'] = now
    state['elapsed'] = min(total_journey, state.get('elapsed', 0.0) + delta_min)
    progress = min(max(state['elapsed'] / total_journey, 0.0), 0.9999)
    segment_float = progress * num_hops
    seg_idx = min(int(segment_float), num_hops - 1)
    seg_t = segment_float - seg_idx
    a = STATION_BY_ID[state['stops'][seg_idx]]
    b = STATION_BY_ID[state['stops'][seg_idx + 1]]
    state['segment_idx'] = seg_idx
    state['segment_t'] = seg_t
    state['progress'] = progress
    state['lat'] = _lerp(a['lat'], b['lat'], seg_t)
    state['lng'] = _lerp(a['lng'], b['lng'], seg_t)


def _train_to_output(state: dict, now: datetime) -> dict:
    tmpl = state['template']
    stops = state['stops']
    seg_idx = state['segment_idx']
    current_sid = stops[seg_idx]
    next_sid = stops[min(seg_idx + 1, len(stops) - 1)]
    current_station = STATION_BY_ID[current_sid]
    next_station = STATION_BY_ID[next_sid]
    crowd_label, crowd_color, crowd_score = predict_crowd(
        tmpl['departs_hour'],
        tmpl['departs_minute'],
        zone=current_station.get('zone', 'central'),
        train_type=tmpl.get('type', 'slow'),
        is_weekend=now.weekday() >= 5,
    )
    eta_minutes = max(1, int((1.0 - state['segment_t']) * tmpl.get('mins_per_hop', 4.0)))
    return {
        'id': tmpl['id'],
        'name': tmpl['name'],
        'display_name': tmpl.get('display_name', tmpl['name']),
        'line': tmpl.get('line', train_line(tmpl)),
        'type': tmpl.get('type', 'slow'),
        'lat': round(state['lat'], 6),
        'lng': round(state['lng'], 6),
        'current_station': current_station['name'],
        'next_station': next_station['name'],
        'current_station_id': current_sid,
        'next_station_id': next_sid,
        'direction': 'UP' if tmpl.get('direction', 1) == 1 else 'DOWN',
        'crowd': crowd_label,
        'crowd_score': crowd_score,
        'crowd_color': crowd_color,
        'color': tmpl.get('color', '#6366F1'),
        'progress': round(state['progress'], 3),
        'eta_minutes': eta_minutes,
        'route_name': tmpl.get('route_name'),
    }


def get_trains_sync() -> List[dict]:
    global _sim, _all_trains, _last_catalogue_load
    now = get_service_now()
    now_minutes = now.hour * 60 + now.minute
    if not _all_trains or (time.time() - _last_catalogue_load) > 300:
        _all_trains = _load_trains_sync()
        _last_catalogue_load = time.time()

    active_templates = _active_for_time(_all_trains, now_minutes)
    active_ids = set()
    for tmpl in active_templates:
        active_ids.add(tmpl['id'])
        if tmpl['id'] not in _sim:
            dep_min = tmpl['departs_hour'] * 60 + tmpl['departs_minute']
            elapsed = (now_minutes - dep_min) % (24 * 60)
            _sim[tmpl['id']] = _init_train_state(tmpl, float(elapsed))
        if _sim.get(tmpl['id']):
            _step_train(_sim[tmpl['id']])

    for tid in list(_sim.keys()):
        if tid not in active_ids:
            del _sim[tid]

    return [_train_to_output(s, now) for s in _sim.values()]


async def get_trains() -> List[dict]:
    results = get_trains_sync()
    try:
        from app.db.trains_db import upsert_live_position
        for r in results:
            await upsert_live_position(r['id'], r)
    except Exception as e:
        logger.debug(f'live position upsert skipped: {e}')
    return results
