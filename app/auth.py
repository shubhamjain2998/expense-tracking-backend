"""JWT authentication.

Every protected endpoint declares `user_id: uuid.UUID = Depends(get_current_user)`.
The dependency extracts the JWT from either the `token` cookie (preferred, set
httpOnly by /auth/login and /auth/register) or the `Authorization: Bearer`
header (legacy path, kept while the frontend still uses localStorage), verifies
it with HS256, and returns the caller's user UUID.

JWT claims relevant here:
  sub  – user UUID (string)
  aud  – "authenticated"
  exp  – expiry unix timestamp
"""

import uuid
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

AUTH_COOKIE_NAME = "token"

# auto_error=False lets us fall back to the cookie when no header is present.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> uuid.UUID:
    """Verify JWT (from cookie or Bearer header) and return the caller's user UUID."""
    token: Optional[str] = None
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(AUTH_COOKIE_NAME)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )
    try:
        return uuid.UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token",
        )
