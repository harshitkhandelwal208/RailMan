"""
GET /api/analytics — Usage stats, popular routes, feedback summary
GET /api/popular_routes — Most searched routes
"""
from fastapi import APIRouter
from app.db.mongo import get_analytics, get_popular_routes

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/analytics")
async def analytics():
    """Full analytics dashboard data."""
    return await get_analytics()


@router.get("/popular_routes")
async def popular_routes():
    """Top 10 most searched routes."""
    return await get_popular_routes()
