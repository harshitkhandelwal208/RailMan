"""
Pydantic models for request/response validation.
"""
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    context: Optional[List[dict]] = Field(default_factory=list)


class RecommendRequest(BaseModel):
    source: str
    destination: str
    time: Optional[str] = None                          
    preference: Literal["fastest", "least_crowded", "balanced"] = "balanced"
    preferred_line: Optional[Literal["western", "central", "harbour", "all"]] = "all"
    train_id: Optional[str] = None


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


             
class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class OAuthCallbackRequest(BaseModel):
    provider: Literal["google", "github"]
    code: str
    redirect_uri: Optional[str] = None


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    provider: str
    created_at: Optional[datetime] = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ClearMemoryRequest(BaseModel):
    session_id: str
    conversation_id: Optional[str] = None


class SettingsRequest(BaseModel):
    theme: Optional[Literal["dark", "light"]] = None
    map_style: Optional[Literal["dark", "satellite", "streets"]] = None
    units: Optional[Literal["metric", "imperial"]] = None
    notifications: Optional[bool] = None
    default_from: Optional[str] = None
    default_to: Optional[str] = None
    language: Optional[Literal["en", "hi", "mr"]] = None
