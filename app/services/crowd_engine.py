"""
Crowd Prediction Engine
-----------------------
Simulates ML-grade crowd forecasting based on:
  - Time of day (peak/off-peak)
  - Day of week
  - Station zone (south stations tend to be more crowded during peak)
  - Train type (fast trains attract more passengers)

Returns one of: Low | Medium | High | Extreme
"""
from typing import Tuple

from app.services.time_utils import get_service_now


# Peak window definitions (hour ranges, inclusive)
MORNING_PEAK = (7, 11)
EVENING_PEAK = (17, 21)
SHOULDER_AM  = (6, 7)
SHOULDER_PM  = (21, 22)

# Crowd score 0-100 mapping to labels
CROWD_LABELS = [
    (0,  25,  "Low",     "#22C55E"),   # green
    (25, 55,  "Medium",  "#EAB308"),   # yellow
    (55, 80,  "High",    "#F97316"),   # orange
    (80, 100, "Extreme", "#EF4444"),   # red
]

ZONE_MULTIPLIER = {
    "south":       1.2,
    "central":     1.1,
    "north":       1.0,
    "far-north":   0.85,
    "east":        1.0,
    "navi-mumbai": 0.92,
}

TYPE_MULTIPLIER = {
    "fast": 1.15,
    "slow": 0.90,
}


def _base_score(hour: int, minute: int = 0) -> float:
    """Return raw crowd score 0-100 based on time."""
    t = hour + minute / 60.0

    if MORNING_PEAK[0] <= hour <= MORNING_PEAK[1]:
        # Gaussian peak centred at 9:00
        centre = 9.0
        peak = 95.0
        width = 1.5
    elif EVENING_PEAK[0] <= hour <= EVENING_PEAK[1]:
        # Gaussian peak centred at 19:00
        centre = 19.0
        peak = 90.0
        width = 1.8
    elif SHOULDER_AM[0] <= hour < SHOULDER_AM[1]:
        centre = 6.5
        peak = 45.0
        width = 0.8
    elif SHOULDER_PM[0] <= hour < SHOULDER_PM[1]:
        centre = 21.5
        peak = 40.0
        width = 0.8
    else:
        # Off-peak: gentle sine-based variation for realism
        centre = t
        peak = 20.0 + 10 * abs((t % 12) - 6) / 6
        width = 999  # effectively flat

    import math
    score = peak * math.exp(-((t - centre) ** 2) / (2 * width ** 2))
    return max(8.0, min(100.0, score))


def predict_crowd(
    hour: int,
    minute: int = 0,
    zone: str = "central",
    train_type: str = "slow",
    is_weekend: bool = False,
) -> Tuple[str, str, int]:
    """
    Returns (label, hex_color, score_0_to_100).
    """
    score = _base_score(hour, minute)
    score *= ZONE_MULTIPLIER.get(zone, 1.0)
    score *= TYPE_MULTIPLIER.get(train_type, 1.0)
    if is_weekend:
        score *= 0.65   # ~35% less crowded on weekends

    score = int(min(100, max(0, score)))

    for lo, hi, label, color in CROWD_LABELS:
        if lo <= score < hi or (hi == 100 and score == 100):
            return label, color, score

    return "Low", "#22C55E", score


def forecast_day(zone: str = "central", train_type: str = "slow", reference_now=None) -> list:
    """
    Return hourly crowd forecast for the full day — used for the chart.
    """
    now = reference_now or get_service_now()
    is_weekend = now.weekday() >= 5
    result = []
    for h in range(0, 24):
        label, color, score = predict_crowd(h, 0, zone, train_type, is_weekend)
        result.append({
            "hour": h,
            "label": label,
            "color": color,
            "score": score,
            "time": f"{h:02d}:00"
        })
    return result
