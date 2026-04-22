"""
GET /api/stations — All supported Mumbai suburban stations (from MongoDB with JSON fallback)
"""
from fastapi import APIRouter
from app.db.trains_db import get_stations

router = APIRouter(prefix="/api", tags=["stations"])


@router.get("/stations")
async def stations():
    """Returns all supported Mumbai suburban stations with real GPS coordinates and line metadata."""
    return await get_stations()
