"""
Pydantic models for request/response validation.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    session_id: Optional[str] = None
    context: Optional[List[dict]] = []


class RecommendRequest(BaseModel):
    source: str
    destination: str
    time: Optional[str] = None          # "HH:MM" format
    preference: Literal["fastest", "least_crowded", "balanced"] = "balanced"


class FeedbackRequest(BaseModel):
    session_id: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class StationOut(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    zone: str
    index: int


class TrainOut(BaseModel):
    id: str
    name: str
    type: str
    lat: float
    lng: float
    current_station: str
    next_station: str
    direction: str
    crowd: str
    crowd_score: int
    color: str
    progress: float


class RecommendationOut(BaseModel):
    best: dict
    alternatives: List[dict]
    explanation: str
    crowd_forecast: dict
