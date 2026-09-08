from dataclasses import dataclass
from uuid import UUID
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from app.config.config import settings
from app.config.db import get_admin_session
from app.models.user import User as UserModel
from sqlalchemy import select

# Calling bcrypt directly, not through passlib's CryptContext: passlib 1.7.4's
# version probe assumes an older bcrypt API (it reads `bcrypt.__about__`,
# removed in bcrypt 4+) and crashes on every hash/verify call in this
# environment. bcrypt itself works fine -- this sidesteps a broken shim.

@dataclass
class User:
    user_id: UUID
    tenant_id: UUID
    role: str

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


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
