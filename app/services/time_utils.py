"""
Shared time helpers for Mumbai Western Line services.
"""
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


_IST_FALLBACK = timezone(timedelta(hours=5, minutes=30), name="IST")


def _configured_timezone_name() -> str:
    return os.getenv("RAILMAN_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"


@lru_cache(maxsize=1)
def get_service_timezone():
    tz_name = _configured_timezone_name()
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return _IST_FALLBACK


def get_service_timezone_name() -> str:
    return _configured_timezone_name()


def get_service_now() -> datetime:
    return datetime.now(get_service_timezone())
