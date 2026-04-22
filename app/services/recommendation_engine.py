"""
Multi-line recommendation engine for Western, Central, and Harbour services.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from app.services.crowd_engine import forecast_day, predict_crowd
from app.services.knowledge_base import get_station_alias_lookup
from app.services.rail_network import (
    INTERCHANGE_STATIONS,
    TRANSFER_MINUTES,
    available_line_paths,
    find_station,
    line_orders,
    load_trains,
    station_lines,
    station_positions,
    train_line,
    train_stop_ids,
)
from app.services.time_utils import get_service_now, get_service_timezone_name

_DATA = Path(__file__).parent.parent / "data"

with open(_DATA / "stations.json", encoding="utf-8") as f:
    STATIONS: List[dict] = json.load(f)

STATION_BY_ID = {s["id"]: s for s in STATIONS}
STATION_BY_NAME = {s["name"].lower(): s for s in STATIONS}
STATION_ALIAS_LOOKUP = get_station_alias_lookup()


def _shared_lines(source_station: dict, destination_station: dict) -> List[str]:
    return [line for line in station_lines(source_station) if line in station_lines(destination_station)]


def _transfer_stations_on_same_line(line: str, source_sid: str, destination_sid: str) -> List[str]:
    order = line_orders().get(line, [])
    if source_sid not in order or destination_sid not in order:
        return []
    src_idx = order.index(source_sid)
    dst_idx = order.index(destination_sid)
    lo, hi = sorted((src_idx, dst_idx))
    return order[lo + 1 : hi]


def _same_line_route_path(source_station: dict, destination_station: dict, line_filter: Optional[str]) -> List[List[str]]:
    paths: List[List[str]] = []
    shared = _shared_lines(source_station, destination_station)
    if line_filter and line_filter in {"western", "central", "harbour"}:
        shared = [line for line in shared if line == line_filter]
    for line in shared:
        paths.append([line, line])
    return paths


def _find_station(q: str) -> Optional[dict]:
    found = find_station(q)
    if found:
        return found
    q = q.strip().lower()
    if q.upper() in STATION_BY_ID:
        return STATION_BY_ID[q.upper()]
    if q in STATION_ALIAS_LOOKUP:
        canonical = STATION_ALIAS_LOOKUP[q]
        if canonical.lower() in STATION_BY_NAME:
            return STATION_BY_NAME[canonical.lower()]
    if q in STATION_BY_NAME:
        return STATION_BY_NAME[q]
    for name, station in STATION_BY_NAME.items():
        if q in name or name in q:
            return station
    return None


def _normalize_line_filter(line: Optional[str]) -> Optional[str]:
    if not line:
        return None
    line = line.strip().lower()
    return line if line in {"western", "central", "harbour", "harbor"} else None


def _train_id_matches(train: dict, preferred_train_id: Optional[str]) -> bool:
    if not preferred_train_id:
        return True
    return (train.get("id") or "").lower() == preferred_train_id.strip().lower()


def _route_rank(route: dict, preference: str) -> tuple:
    best = route.get("best", {})
    arrival = best.get("arrival_minutes", (best.get("wait_minutes", 0) + best.get("travel_minutes", 999)))
    wait = best.get("wait_minutes", 999)
    crowd_score = best.get("crowd_score", 999)
    transfer_minutes = best.get("transfer_minutes", 0) if best.get("kind") == "transfer" else 0
    kind_priority = 0 if best.get("kind") == "direct" else 1
    if preference == "fastest":
        return (arrival, wait, crowd_score, transfer_minutes, kind_priority)
    if preference == "least_crowded":
        return (crowd_score, arrival, wait, transfer_minutes, kind_priority)
    return (arrival, crowd_score, wait, transfer_minutes, kind_priority)


def _attach_route_metadata(best: dict, kind: str) -> dict:
    enriched = dict(best)
    enriched.setdefault("kind", kind)
    enriched.setdefault("arrival_minutes", enriched.get("wait_minutes", 0) + enriched.get("travel_minutes", 0))
    if kind == "direct":
        enriched.setdefault("legs", [_make_leg_summary(best)])
    return enriched


def _merge_alternatives(*route_sets: dict) -> list:
    merged = []
    seen = set()
    for route in route_sets:
        for alt in route.get("alternatives", []):
            key = (alt.get("kind"), alt.get("name"), alt.get("departs"), alt.get("transfer_station"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(alt)
    return merged[:3]


def _train_serves(train: dict, src_sid: str, dst_sid: str) -> bool:
    stops = train_stop_ids(train)
    if not stops or src_sid not in stops or dst_sid not in stops:
        return False
    try:
        return stops.index(src_sid) < stops.index(dst_sid)
    except ValueError:
        return False


def _estimate_minutes(train: dict, src_sid: str, dst_sid: str) -> int:
    stops = train_stop_ids(train)
    if not stops:
        return 999
    try:
        hops = abs(stops.index(dst_sid) - stops.index(src_sid))
    except ValueError:
        return 999
    mins_per_hop = train.get(
        "mins_per_hop",
        {"fast": 3.0, "semi": 3.5, "slow": 4.0}.get(train.get("type", "slow"), 3.5),
    )
    return max(3, int(hops * mins_per_hop))


def _parse_query_time(time_str: Optional[str]) -> tuple[int, int]:
    now = get_service_now()
    if not time_str:
        return now.hour, now.minute
    try:
        hour, minute = map(int, time_str.split(":"))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    except Exception:
        pass
    return now.hour, now.minute


def _source_departure_minutes(train: dict, src_sid: str) -> int:
    dep_hour = train.get("departs_hour", 0)
    dep_minute = train.get("departs_minute", 0)
    stops = train_stop_ids(train)
    start_sid = train.get("start_station_id") or (stops[0] if stops else None)
    if not start_sid or start_sid not in stops or src_sid not in stops:
        return dep_hour * 60 + dep_minute
    try:
        start_i = stops.index(start_sid)
        src_i = stops.index(src_sid)
    except ValueError:
        return dep_hour * 60 + dep_minute
    mins_per_hop = train.get(
        "mins_per_hop",
        {"fast": 3.0, "semi": 3.5, "slow": 4.0}.get(train.get("type", "slow"), 3.5),
    )
    return (dep_hour * 60 + dep_minute + int(max(0, src_i - start_i) * mins_per_hop)) % (24 * 60)


def _score_train(train: dict, src_sid: str, dst_sid: str, hour: int, minute: int, preference: str, zone: str, reference_now):
    src_dep_total = _source_departure_minutes(train, src_sid)
    src_dep_hour = src_dep_total // 60
    src_dep_minute = src_dep_total % 60
    request_total = hour * 60 + minute
    wait_minutes = (src_dep_total - request_total) % (24 * 60)
    departure_day_offset = 0 if src_dep_total >= request_total else 1
    is_weekend = ((reference_now.weekday() + departure_day_offset) % 7) >= 5
    crowd_label, crowd_color, crowd_score = predict_crowd(
        src_dep_hour,
        src_dep_minute,
        zone=zone,
        train_type=train.get("type", "slow"),
        is_weekend=is_weekend,
    )
    travel_minutes = _estimate_minutes(train, src_sid, dst_sid)
    crowd_score_norm = 1.0 - crowd_score / 100.0
    travel_score_norm = max(0.0, 1.0 - travel_minutes / 120.0)
    weights = {
        "fastest": (0.20, 0.80),
        "least_crowded": (0.80, 0.20),
        "balanced": (0.50, 0.50),
    }.get(preference, (0.50, 0.50))
    composite = (weights[0] * crowd_score_norm) + (weights[1] * travel_score_norm)
    composite -= (wait_minutes / (24 * 60.0)) * 0.1
    return composite, {
        "id": train["id"],
        "name": train["name"],
        "display_name": train.get("display_name", train["name"]),
        "line": train_line(train),
        "type": train.get("type", "slow"),
        "color": train.get("color", "#6366F1"),
        "crowd": crowd_label,
        "crowd_color": crowd_color,
        "crowd_score": crowd_score,
        "travel_minutes": travel_minutes,
        "departs": f"{src_dep_hour:02d}:{src_dep_minute:02d}",
        "wait_minutes": wait_minutes,
        "departure_day_offset": departure_day_offset,
        "composite_score": round(composite, 3),
        "start_station_id": train.get("start_station_id"),
        "end_station_id": train.get("end_station_id"),
    }


def _sort_candidates(scored: List[dict], preference: str) -> List[dict]:
    if preference == "least_crowded":
        scored.sort(key=lambda item: (item["departure_day_offset"], item["wait_minutes"] // 30, item["wait_minutes"] if item["departure_day_offset"] else 0, item["crowd_score"], item["travel_minutes"], item["id"]))
    elif preference == "fastest":
        scored.sort(key=lambda item: (item["departure_day_offset"], item["wait_minutes"] // 30, item["wait_minutes"] if item["departure_day_offset"] else 0, item["travel_minutes"], item["crowd_score"], item["id"]))
    else:
        scored.sort(key=lambda item: (item["departure_day_offset"], item["wait_minutes"] // 30, item["wait_minutes"] if item["departure_day_offset"] else 0, item["crowd_score"], item["travel_minutes"], item["id"]))
    return scored


def _leg_candidates(trains: List[dict], src_sid: str, dst_sid: str, hour: int, minute: int, preference: str, zone: str, reference_now) -> List[dict]:
    scored = []
    for train in trains:
        if not _train_serves(train, src_sid, dst_sid):
            continue
        score, meta = _score_train(train, src_sid, dst_sid, hour, minute, preference, zone, reference_now)
        meta["score"] = score
        scored.append(meta)
    return _sort_candidates(scored, preference)


def _make_leg_summary(leg: dict) -> dict:
    return {
        "train_id": leg["id"],
        "train_name": leg.get("display_name", leg["name"]),
        "line": leg["line"],
        "type": leg["type"],
        "departs": leg["departs"],
        "wait_minutes": leg["wait_minutes"],
        "travel_minutes": leg["travel_minutes"],
        "crowd": leg["crowd"],
        "crowd_color": leg["crowd_color"],
        "crowd_score": leg["crowd_score"],
        "departure_day_offset": leg["departure_day_offset"],
        "origin_station_id": leg.get("start_station_id"),
        "destination_station_id": leg.get("end_station_id"),
    }


def _direct_route(trains, source, destination, time_str, preference, reference_now, preferred_line=None, preferred_train_id=None):
    hour, minute = _parse_query_time(time_str)
    src_station = _find_station(source)
    dst_station = _find_station(destination)
    if not src_station:
        return {"error": f"Station '{source}' not found in the RailMan network."}
    if not dst_station:
        return {"error": f"Station '{destination}' not found in the RailMan network."}
    if src_station["id"] == dst_station["id"]:
        return {"error": "Source and destination are the same station."}

    line_filter = _normalize_line_filter(preferred_line)
    shared_lines = [line for line in station_lines(src_station) if line in station_lines(dst_station)]
    if line_filter:
        shared_lines = [line for line in shared_lines if line == line_filter]
    scored: List[dict] = []
    for line in shared_lines:
        line_trains = [t for t in trains if train_line(t) == line]
        if preferred_train_id:
            preferred_only = [t for t in line_trains if _train_id_matches(t, preferred_train_id)]
            if preferred_only:
                line_trains = preferred_only
        scored.extend(_leg_candidates(line_trains, src_station["id"], dst_station["id"], hour, minute, preference, src_station.get("zone", "central"), reference_now))

    if not scored:
        return {"error": "No direct trains found for this route at the requested time."}

    best = _attach_route_metadata(scored[0], "direct")
    alternatives = scored[1:3]
    crowd_emoji = {"Low": "Green", "Medium": "Yellow", "High": "Orange", "Extreme": "Red"}.get(best["crowd"], "White")
    next_day_note = " (next day)" if best["departure_day_offset"] else ""
    explanation = (
        f"Next departure: **{best['name']}** from **{src_station['name']}** to **{dst_station['name']}** after **{hour:02d}:{minute:02d}**.\n\n"
        f"Departs at **{best['departs']}**{next_day_note} and reaches in about **{best['travel_minutes']} min**. "
        f"Wait time is **{best['wait_minutes']} min**. Crowd: **{best['crowd']}** ({crowd_emoji})."
    )
    return {
        "best": best,
        "alternatives": alternatives,
        "explanation": explanation,
        "crowd_forecast": forecast_day(zone=src_station.get("zone", "central"), train_type=best["type"], reference_now=reference_now),
        "meta": {"source": src_station["name"], "destination": dst_station["name"], "query_time": f"{hour:02d}:{minute:02d}", "preference": preference, "trains_evaluated": len(scored), "service_timezone": get_service_timezone_name(), "route_type": "direct"},
    }


def _transfer_route(trains, source, destination, time_str, preference, reference_now, preferred_line=None, preferred_train_id=None):
    hour, minute = _parse_query_time(time_str)
    start_station = _find_station(source)
    end_station = _find_station(destination)
    if not start_station:
        return {"error": f"Station '{source}' not found in the RailMan network."}
    if not end_station:
        return {"error": f"Station '{destination}' not found in the RailMan network."}
    if start_station["id"] == end_station["id"]:
        return {"error": "Source and destination are the same station."}

    line_filter = _normalize_line_filter(preferred_line)
    paths = available_line_paths(station_lines(start_station), station_lines(end_station))

    # Same-line train switching (e.g. slow to fast at Bandra) is modeled as
    # a two-leg transfer on the same line.
    paths.extend(_same_line_route_path(start_station, end_station, line_filter))

    if line_filter:
        filtered_paths = []
        for p in paths:
            if len(p) == 2 and p[0] == p[1] == line_filter:
                filtered_paths.append(p)
            elif line_filter in p:
                filtered_paths.append(p)
        paths = filtered_paths

    if not paths:
        return {"error": "No route path found between those stations."}

    route_candidates = []
    source_min = hour * 60 + minute
    for path in paths:
        if len(path) == 2 and path[0] == path[1]:
            line = path[0]
            transfer_stations = _transfer_stations_on_same_line(line, start_station["id"], end_station["id"])
            for interchange in transfer_stations:
                first_line_trains = [t for t in trains if train_line(t) == line]
                if preferred_train_id:
                    preferred_first = [t for t in first_line_trains if _train_id_matches(t, preferred_train_id)]
                    if preferred_first:
                        first_line_trains = preferred_first
                first_leg = _leg_candidates(first_line_trains, start_station["id"], interchange, hour, minute, preference, start_station.get("zone", "central"), reference_now)
                if not first_leg:
                    continue
                chosen_first = first_leg[0]
                first_arrival = source_min + chosen_first["wait_minutes"] + chosen_first["travel_minutes"] + TRANSFER_MINUTES.get(interchange, 4)
                second_clock = first_arrival % (24 * 60)
                second_line_trains = [t for t in trains if train_line(t) == line]
                if preferred_train_id:
                    preferred_second = [t for t in second_line_trains if _train_id_matches(t, preferred_train_id)]
                    if preferred_second:
                        second_line_trains = preferred_second
                second_leg = _leg_candidates(second_line_trains, interchange, end_station["id"], second_clock // 60, second_clock % 60, preference, end_station.get("zone", "central"), reference_now)
                if not second_leg:
                    continue
                chosen_second = second_leg[0]
                total_wait = chosen_first["wait_minutes"] + chosen_second["wait_minutes"]
                total_travel = chosen_first["travel_minutes"] + chosen_second["travel_minutes"]
                transfer_minutes = TRANSFER_MINUTES.get(interchange, 4)
                total_elapsed = total_wait + total_travel + transfer_minutes
                route_candidates.append({
                    "score": (chosen_first["score"] + chosen_second["score"]) / 2.0 - (transfer_minutes / 60.0),
                    "kind": "transfer",
                    "line_path": path,
                    "transfer_station": interchange,
                    "transfer_minutes": transfer_minutes,
                    "travel_minutes": total_travel,
                    "wait_minutes": total_wait,
                    "departure_day_offset": max(chosen_first["departure_day_offset"], chosen_second["departure_day_offset"]),
                    "departs": chosen_first["departs"],
                    "arrival_minutes": total_elapsed,
                    "crowd": chosen_first["crowd"] if preference != "least_crowded" else chosen_second["crowd"],
                    "crowd_color": chosen_first["crowd_color"],
                    "crowd_score": min(chosen_first["crowd_score"], chosen_second["crowd_score"]),
                    "legs": [_make_leg_summary(chosen_first), _make_leg_summary(chosen_second)],
                })
            continue

        if len(path) != 3:
            continue
        first_line, transfer_line, last_line = path
        first_opts = INTERCHANGE_STATIONS.get((first_line, transfer_line), [])
        second_opts = INTERCHANGE_STATIONS.get((transfer_line, last_line), [])
        interchanges = [sid for sid in first_opts if sid in second_opts] or list(dict.fromkeys(first_opts + second_opts))
        for interchange in interchanges:
            if interchange not in STATION_BY_ID:
                continue
            first_line_trains = [t for t in trains if train_line(t) == first_line]
            if preferred_train_id:
                preferred_first = [t for t in first_line_trains if _train_id_matches(t, preferred_train_id)]
                if preferred_first:
                    first_line_trains = preferred_first
            first_leg = _leg_candidates(first_line_trains, start_station["id"], interchange, hour, minute, preference, start_station.get("zone", "central"), reference_now)
            if not first_leg:
                continue
            chosen_first = first_leg[0]
            first_arrival = source_min + chosen_first["wait_minutes"] + chosen_first["travel_minutes"] + TRANSFER_MINUTES.get(interchange, 6)
            second_clock = first_arrival % (24 * 60)
            second_line_trains = [t for t in trains if train_line(t) == last_line]
            if preferred_train_id:
                preferred_second = [t for t in second_line_trains if _train_id_matches(t, preferred_train_id)]
                if preferred_second:
                    second_line_trains = preferred_second
            second_leg = _leg_candidates(second_line_trains, interchange, end_station["id"], second_clock // 60, second_clock % 60, preference, end_station.get("zone", "central"), reference_now)
            if not second_leg:
                continue
            chosen_second = second_leg[0]
            total_wait = chosen_first["wait_minutes"] + chosen_second["wait_minutes"]
            total_travel = chosen_first["travel_minutes"] + chosen_second["travel_minutes"]
            transfer_minutes = TRANSFER_MINUTES.get(interchange, 6)
            total_elapsed = total_wait + total_travel + transfer_minutes
            route_candidates.append({
                "score": (chosen_first["score"] + chosen_second["score"]) / 2.0 - (transfer_minutes / 60.0),
                "kind": "transfer",
                "line_path": path,
                "transfer_station": interchange,
                "transfer_minutes": transfer_minutes,
                "travel_minutes": total_travel,
                "wait_minutes": total_wait,
                "departure_day_offset": max(chosen_first["departure_day_offset"], chosen_second["departure_day_offset"]),
                "departs": chosen_first["departs"],
                "arrival_minutes": total_elapsed,
                "crowd": chosen_first["crowd"] if preference != "least_crowded" else chosen_second["crowd"],
                "crowd_color": chosen_first["crowd_color"],
                "crowd_score": min(chosen_first["crowd_score"], chosen_second["crowd_score"]),
                "legs": [_make_leg_summary(chosen_first), _make_leg_summary(chosen_second)],
            })

    if not route_candidates:
        return {"error": "No transfer route found for that trip at the requested time."}

    route_candidates.sort(key=lambda item: (item["arrival_minutes"], -item["score"], item["transfer_minutes"]))
    best = route_candidates[0]
    alternatives = route_candidates[1:3]
    if len(best.get("legs", [])) == 2:
        if len(best.get("line_path", [])) == 2 and best["line_path"][0] == best["line_path"][1]:
            explanation = (
                f"Best **same-line switch**: **{best['legs'][0]['train_name']}** from **{start_station['name']}** to **{STATION_BY_ID[best['transfer_station']]['name']}**, "
                f"then **{best['legs'][1]['train_name']}** to **{end_station['name']}**. "
                f"Switch at **{STATION_BY_ID[best['transfer_station']]['name']}** takes about **{best['transfer_minutes']} min**."
            )
        else:
            explanation = (
                f"Best route: **{best['legs'][0]['train_name']}** from **{start_station['name']}** to **{STATION_BY_ID[best['transfer_station']]['name']}**, "
                f"then **{best['legs'][1]['train_name']}** to **{end_station['name']}**. "
                f"Transfer at **{STATION_BY_ID[best['transfer_station']]['name']}** takes about **{best['transfer_minutes']} min**."
            )
    else:
        explanation = "Best route found with a transfer, but route details are unavailable."
    return {
        "best": best,
        "alternatives": alternatives,
        "explanation": explanation,
        "crowd_forecast": forecast_day(zone=start_station.get("zone", "central"), train_type=best["legs"][0]["type"], reference_now=reference_now),
        "meta": {"source": start_station["name"], "destination": end_station["name"], "query_time": f"{hour:02d}:{minute:02d}", "preference": preference, "trains_evaluated": len(route_candidates), "service_timezone": get_service_timezone_name(), "route_type": "transfer"},
    }


def _recommend_with_trains(trains, source, destination, time_str=None, preference="balanced", preferred_line=None, preferred_train_id=None) -> dict:
    reference_now = get_service_now()
    direct = _direct_route(trains, source, destination, time_str, preference, reference_now, preferred_line, preferred_train_id)
    transfer = _transfer_route(trains, source, destination, time_str, preference, reference_now, preferred_line, preferred_train_id)

    if "error" in direct and "error" in transfer:
        return direct
    if "error" in transfer:
        return direct
    if "error" in direct:
        return transfer

    if preference == "balanced":
        chosen, other = (direct, transfer) if direct.get("best", {}).get("kind") == "direct" else (transfer, direct)
    else:
        chosen = direct if _route_rank(direct, preference) <= _route_rank(transfer, preference) else transfer
        other = transfer if chosen is direct else direct

    chosen = dict(chosen)
    chosen["alternatives"] = _merge_alternatives(chosen, other)
    return chosen


async def recommend(source: str, destination: str, time_str: Optional[str] = None, preference: str = "balanced", preferred_line: Optional[str] = None, train_id: Optional[str] = None) -> dict:
    # Bundled JSON is the source of truth; DB is only a fallback if the files are unavailable.
    trains = load_trains()
    if not trains:
        try:
            from app.db.trains_db import get_all_trains
            trains = await get_all_trains()
        except Exception:
            trains = []
    return _recommend_with_trains(trains, source, destination, time_str, preference, preferred_line, train_id)


def recommend_sync(source, destination, time_str=None, preference="balanced", preferred_line=None, train_id=None) -> dict:
    return _recommend_with_trains(load_trains(), source, destination, time_str, preference, preferred_line, train_id)


def extract_entities(text: str) -> dict:
    text_lower = text.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", text_lower)
    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text_lower)
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
    if any(word in text_lower for word in ["fast", "quick", "earliest", "least time", "express"]):
        preference = "fastest"
    elif any(word in text_lower for word in [
        "less crowd",
        "least crowd",
        "empty",
        "comfortable",
        "uncrowded",
        "don't mind waiting",
        "dont mind waiting",
        "can wait",
        "wait longer",
        "rather wait",
    ]):
        preference = "least_crowded"

    preferred_line = None
    if "western line" in text_lower or re.search(r"\bwestern\b", text_lower):
        preferred_line = "western"
    elif "central line" in text_lower or re.search(r"\bcentral\b", text_lower):
        preferred_line = "central"
    elif "harbour line" in text_lower or "harbor line" in text_lower or re.search(r"\bharbour\b|\bharbor\b", text_lower):
        preferred_line = "harbour"

    found = []
    for station in STATIONS:
        name = station["name"].lower()
        if name in text_lower:
            found.append((text_lower.find(name), station))
    for alias, canonical in STATION_ALIAS_LOOKUP.items():
        match = re.search(rf"\b{re.escape(alias)}\b", text_lower)
        station = STATION_BY_NAME.get(canonical.lower())
        if match and station:
            found.append((match.start(), station))
    found.sort(key=lambda item: item[0])

    ordered = []
    seen = set()
    for _, station in found:
        if station["name"] in seen:
            continue
        seen.add(station["name"])
        ordered.append(station)

    source = ordered[0]["name"] if len(ordered) > 0 else None
    destination = ordered[1]["name"] if len(ordered) > 1 else None

    from_to_match = re.search(r"from\s+(\w[\w\s]*?)\s+to\s+(\w[\w\s]+)", text_lower)
    if from_to_match:
        src_candidate = _find_station(from_to_match.group(1).strip())
        dst_candidate = _find_station(from_to_match.group(2).strip())
        if src_candidate:
            source = src_candidate["name"]
        if dst_candidate:
            destination = dst_candidate["name"]

    return {
        "source": source,
        "destination": destination,
        "time": time_str,
        "preference": preference,
        "preferred_line": preferred_line,
        "train_id": None,
    }
