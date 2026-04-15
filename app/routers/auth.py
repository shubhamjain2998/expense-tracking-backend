from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_TOKEN_TTL_DAYS = 30


class AuthRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _make_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "exp": datetime.now(timezone.utc) + timedelta(days=_TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: AuthRequest, db: Session = Depends(get_db)):
    existing = db.execute(
        select(User).where(User.email == body.email.lower())
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email.lower(),
        password_hash=_pwd.hash(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=_make_token(str(user.id)))


@router.post("/login", response_model=TokenResponse)
def login(body: AuthRequest, db: Session = Depends(get_db)):
    user = db.execute(
        select(User).where(User.email == body.email.lower())
    ).scalar_one_or_none()
    if user is None or not _pwd.verify(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    return TokenResponse(access_token=_make_token(str(user.id)))
