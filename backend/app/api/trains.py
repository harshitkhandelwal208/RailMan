"""
GET /api/live_trains      — Simulated live train positions (written to MongoDB)
GET /api/crowd_forecast   — 24h crowd prediction chart data
GET /api/train_catalogue  — Full train timetable from MongoDB
"""
from fastapi import APIRouter, Query
from typing import Optional

from app.services.simulator import get_trains
from app.services.crowd_engine import forecast_day
from app.db.mongo import get_all_trains

router = APIRouter(prefix="/api", tags=["trains"])


@router.get("/live_trains")
async def live_trains():
    """Live positions for all active trains (up to 30 concurrent)."""
    return await get_trains()


@router.get("/crowd_forecast")
def crowd_forecast(
    zone:       Optional[str] = Query("central"),
    train_type: Optional[str] = Query("slow"),
):
    """24-hour crowd forecast for charting."""
    return forecast_day(zone=zone, train_type=train_type)


@router.get("/train_catalogue")
async def train_catalogue():
    """Full timetable of all 388 trains from MongoDB."""
    trains = await get_all_trains()
    return {
        "total": len(trains),
        "trains": trains,
    }
