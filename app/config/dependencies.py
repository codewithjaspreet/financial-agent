from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from app.config.config import settings
from app.services.auth import User
from uuid import UUID
security = HTTPBearer()

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> User:
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token")

    return User(
        user_id=UUID(payload["user_id"]),
        tenant_id=UUID(payload["tenant_id"]),
        role=payload["role"],
    )
