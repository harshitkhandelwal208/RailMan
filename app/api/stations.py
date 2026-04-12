"""
GET /api/stations — All 28 Western Line stations (from MongoDB with JSON fallback)
"""
from fastapi import APIRouter
from app.db.trains_db import get_stations

router = APIRouter(prefix="/api", tags=["stations"])


@router.get("/stations")
async def stations():
    """Returns all 28 Western Line stations with real GPS coordinates."""
    return await get_stations()
