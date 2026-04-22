"""
Shared railway-network helpers for RailMan.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence

DATA = Path(__file__).parent.parent / "data"


@lru_cache(maxsize=1)
def load_stations() -> List[dict]:
    with open(DATA / "stations.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_trains() -> List[dict]:
    with open(DATA / "trains.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_aliases() -> Dict[str, List[str]]:
    with open(DATA / "station_aliases.json", encoding="utf-8") as f:
        return json.load(f)


def invalidate_cache() -> None:
    load_stations.cache_clear()
    load_trains.cache_clear()
    load_aliases.cache_clear()
    station_by_id.cache_clear()
    station_by_name.cache_clear()
    alias_lookup.cache_clear()
    line_orders.cache_clear()


@lru_cache(maxsize=1)
def station_by_id() -> Dict[str, dict]:
    return {s["id"]: s for s in load_stations()}


@lru_cache(maxsize=1)
def station_by_name() -> Dict[str, dict]:
    return {s["name"].lower(): s for s in load_stations()}


@lru_cache(maxsize=1)
def alias_lookup() -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for canonical, aliases in load_aliases().items():
        lookup[canonical.lower()] = canonical
        for alias in aliases:
            lookup[alias.lower()] = canonical
    return lookup


@lru_cache(maxsize=1)
def line_orders() -> Dict[str, List[str]]:
    stations = load_stations()
    result: Dict[str, List[str]] = {"western": [], "central": [], "harbour": []}
    for line in result:
        ids = [s["id"] for s in stations if line in s.get("lines", [])]
        ids.sort(key=lambda sid: station_by_id()[sid].get("line_positions", {}).get(line, 999))
        result[line] = ids
    return result


INTERCHANGE_STATIONS: Dict[tuple[str, str], List[str]] = {
    ("western", "central"): ["DR"],
    ("central", "western"): ["DR"],
    ("central", "harbour"): ["KRL", "SND", "CSMT"],
    ("harbour", "central"): ["KRL", "SND", "CSMT"],
}

TRANSFER_MINUTES = {
    "DR": 7,
    "KRL": 6,
    "SND": 6,
    "CSMT": 7,
}


def train_line(train: dict) -> str:
    line = train.get("line")
    if line in ("western", "central", "harbour"):
        return line
    tid = (train.get("id") or "").upper()
    if tid.startswith("WR-"):
        return "western"
    if tid.startswith("CL-"):
        return "central"
    if tid.startswith("HB-"):
        return "harbour"
    return "western"


def train_stop_ids(train: dict) -> List[str]:
    stop_ids = train.get("stop_ids")
    if stop_ids:
        return list(stop_ids)
    stop_indices = train.get("stop_indices") or []
    line = train_line(train)
    order = line_orders().get(line, [])
    out = []
    for idx in stop_indices:
        if 0 <= idx < len(order):
            out.append(order[idx])
    return out


def find_station(query: str) -> Optional[dict]:
    q = (query or "").strip().lower()
    if not q:
        return None
    if q.upper() in station_by_id():
        return station_by_id()[q.upper()]
    if q in alias_lookup():
        canonical = alias_lookup()[q]
        if canonical.lower() in station_by_name():
            return station_by_name()[canonical.lower()]
    if q in station_by_name():
        return station_by_name()[q]
    for name, station in station_by_name().items():
        if q in name or name in q:
            return station
    return None


def station_lines(station: dict) -> List[str]:
    return list(station.get("lines", []))


def station_positions(station: dict) -> Dict[str, int]:
    return dict(station.get("line_positions", {}))


def available_line_paths(source_lines: Sequence[str], destination_lines: Sequence[str]) -> List[List[str]]:
    adjacency = {
        "western": ["central"],
        "central": ["western", "harbour"],
        "harbour": ["central"],
    }
    from collections import deque

    paths: List[List[str]] = []
    for src in source_lines:
        for dst in destination_lines:
            q = deque([(src, [src])])
            seen = {src}
            while q:
                node, path = q.popleft()
                if node == dst:
                    paths.append(path)
                    break
                for nxt in adjacency.get(node, []):
                    if nxt in seen:
                        continue
                    if len(path) >= 3:
                        continue
                    seen.add(nxt)
                    q.append((nxt, path + [nxt]))
    uniq: List[List[str]] = []
    seen_paths = set()
    for p in paths:
        tp = tuple(p)
        if tp not in seen_paths:
            seen_paths.add(tp)
            uniq.append(p)
    return uniq
