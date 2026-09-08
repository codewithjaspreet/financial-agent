from dataclasses import dataclass
from uuid import UUID
import jwt
from datetime import datetime, timedelta, timezone
from app.config.config import settings
from app.config.db import get_admin_session
from app.models.user import User as UserModel
from sqlalchemy import select
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@dataclass
class User:
    user_id: UUID
    tenant_id: UUID
    role: str

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def login(email: str, password: str) -> str:
    session = get_admin_session()
    user = session.scalar(select(UserModel).where(UserModel.email == email))
    if not user or not verify_password(password, user.password_hash):
        raise ValueError("invalid email or password")

    payload = {
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=12),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
