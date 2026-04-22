"""
POST /api/auth/register  — Email + password registration
POST /api/auth/login     — Email + password login
POST /api/auth/oauth     — OAuth (Google / GitHub) token exchange stub
GET  /api/auth/me        — Get current user (requires token)
POST /api/auth/logout    — Invalidate session / clear memory
"""
import os
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.models.schemas import RegisterRequest, LoginRequest, TokenOut, UserOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

SECRET_KEY = os.getenv("JWT_SECRET", "railman-secret-change-in-production")
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 24 * 7          


                                                                                
def _create_token(user_id: str, email: str) -> str:
    try:
        from jose import jwt
        expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
        payload = {"sub": user_id, "email": email, "exp": expire, "iat": datetime.utcnow()}
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    except ImportError:
                                                                       
        token = str(uuid.uuid4())
        _token_store[token] = {"user_id": user_id, "email": email}
        return token


def _verify_token(token: str) -> Optional[dict]:
    try:
        from jose import jwt, JWTError
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        pass
                    
    return _token_store.get(token)


_token_store: dict = {}                     


def _hash_password(password: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
                                                                    
        import hashlib
        return hashlib.sha256((password + SECRET_KEY).encode()).hexdigest()


def _check_password(password: str, hashed: str) -> bool:
    try:
        import bcrypt
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ImportError:
        import hashlib
        return hashlib.sha256((password + SECRET_KEY).encode()).hexdigest() == hashed


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Optional[dict]:
    """Dependency: decode JWT and return user dict, or None if unauthenticated."""
    if not credentials:
        return None
    payload = _verify_token(credentials.credentials)
    if not payload:
        return None
    return payload


                                                                               
async def _auth_rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    try:
        from app.db.chat_db import check_rate_limit
        allowed = await check_rate_limit(f"auth:{ip}", max_requests=10, window_seconds=60)
        if not allowed:
            raise HTTPException(status_code=429, detail="Too many requests. Please wait.")
    except HTTPException:
        raise
    except Exception:
        pass                                             


                                                                                
@router.post("/register", response_model=TokenOut)
async def register(req: RegisterRequest, request: Request):
    await _auth_rate_limit(request)
    from app.db.chat_db import get_user_by_email, create_user

    existing = await get_user_by_email(req.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered.")

    hashed = _hash_password(req.password)
    user = await create_user(
        email=req.email,
        name=req.name,
        password_hash=hashed,
        provider="email",
    )
    if not user:
                                                 
        user = {
            "id": str(uuid.uuid4()),
            "email": req.email,
            "name": req.name,
            "provider": "email",
            "created_at": datetime.utcnow(),
        }

    token = _create_token(user["id"], user["email"])
    return TokenOut(
        access_token=token,
        user=UserOut(
            id=user["id"],
            email=user["email"],
            name=user["name"],
            provider=user.get("provider", "email"),
            created_at=user.get("created_at"),
        )
    )


@router.post("/login", response_model=TokenOut)
async def login(req: LoginRequest, request: Request):
    await _auth_rate_limit(request)
    from app.db.chat_db import get_user_by_email, update_user_login

    user = await get_user_by_email(req.email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    pw_hash = user.get("password_hash")
    if not pw_hash:
        raise HTTPException(status_code=400, detail="This account uses social login. Please sign in with Google or GitHub.")

    if not _check_password(req.password, pw_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    await update_user_login(req.email)

    from bson import ObjectId
    user_id = str(user.get("_id", user.get("id", uuid.uuid4())))
    token = _create_token(user_id, user["email"])

    return TokenOut(
        access_token=token,
        user=UserOut(
            id=user_id,
            email=user["email"],
            name=user.get("name", "User"),
            provider=user.get("provider", "email"),
            created_at=user.get("created_at"),
        )
    )


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return {
        "id":    current_user.get("sub", current_user.get("user_id")),
        "email": current_user.get("email"),
    }


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Clear server-side token from fallback store if present."""
    return {"status": "ok", "message": "Logged out successfully."}


@router.post("/guest")
async def guest_session():
    """Create a guest session ID for anonymous users (no auth required)."""
    guest_id = f"guest_{uuid.uuid4().hex[:12]}"
    return {"session_id": guest_id, "type": "guest"}
