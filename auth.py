"""认证工具：密码哈希（pbkdf2_hmac）+ JWT 签发/校验 + 鉴权依赖。"""
import hashlib
import hmac
import os
import time
from typing import Optional

import jwt
from fastapi import Header, HTTPException, status

# 生产环境应从环境变量读取；此处用固定值仅用于开发演示
JWT_SECRET = os.getenv("JWT_SECRET", "trip_ledger_dev_secret_change_me")
JWT_ALG = "HS256"
JWT_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 天

PBKDF2_ITERATIONS = 100_000
SALT_LEN = 16


def hash_password(password: str) -> str:
    """返回 salt$hash 格式的字符串。"""
    salt = os.urandom(SALT_LEN)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk, expected)


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": int(time.time()) + JWT_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None


def get_current_user(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    """从 Authorization: Bearer <token> 解析当前用户；未登录返回 None（不强制）。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    payload = decode_token(token)
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    # 延迟导入避免循环
    from trip_planner.models import get_user_by_id
    return get_user_by_id(user_id)


def require_auth(authorization: Optional[str] = Header(default=None)) -> dict:
    """强制登录的依赖。"""
    user = get_current_user(authorization)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


def require_merchant(authorization: Optional[str] = Header(default=None)) -> dict:
    user = require_auth(authorization)
    if user["role"] != "merchant":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅企业用户可访问")
    return user
