from __future__ import annotations

import base64
import hashlib
import hmac
import os

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User, UserRole


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return "pbkdf2_sha256$390000$%s$%s" % (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    login = email.strip().lower()
    user = db.scalar(select(User).where(or_(User.email == login, User.username == login), User.active.is_(True)))
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def ensure_bootstrap_admin(db: Session) -> User:
    email = settings.bootstrap_admin_email.strip().lower()
    username = email.split("@", 1)[0]
    user = db.scalar(select(User).where(or_(User.username == username, User.email == email)))
    if user:
        if user.role != UserRole.ADMIN or not user.active:
            user.role = UserRole.ADMIN
            user.active = True
            db.commit()
            db.refresh(user)
        return user
    user = User(
        email=email,
        username=username,
        first_name=settings.bootstrap_admin_name,
        last_name="",
        name=settings.bootstrap_admin_name,
        timezone="America/Chicago",
        role=UserRole.ADMIN,
        password_hash=hash_password(settings.bootstrap_admin_password),
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
