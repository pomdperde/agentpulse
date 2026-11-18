"""认证与授权：JWT（标准库实现）+ PBKDF2 密码哈希，零第三方依赖。

设计：
- 密码用 hashlib.pbkdf2_hmac（sha256, 100k 轮）+ 随机盐
- JWT 用 hmac.new(sha256) 手写 HS256，base64url 编码
- 依赖注入 get_current_user / require_admin，供路由保护使用
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agentpulse.infra import db
from agentpulse.core.config import Settings

security = HTTPBearer(auto_error=False)

PBKDF2_ITERATIONS = 100_000
DEFAULT_TOKEN_EXPIRE_DAYS = 7

# 未配置 AUTH_SECRET 时使用的进程级随机密钥（生成后缓存，避免每次调用不同）
_fallback_secret: str | None = None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (hash_hex, salt_hex)。"""
    if salt is None:
        salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return derived.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(derived.hex(), password_hash)


def _get_secret() -> str:
    global _fallback_secret
    settings = Settings()
    secret = settings.auth_secret
    if secret:
        return secret
    # 未配置时生成进程级随机密钥并缓存（重启后所有 token 失效，生产环境务必配置 AUTH_SECRET）
    if _fallback_secret is None:
        _fallback_secret = os.environ.get("AUTH_SECRET") or secrets.token_hex(32)
    return _fallback_secret


def create_token(user_id: int, username: str, role: str) -> str:
    """生成 JWT access token。"""
    now = int(time.time())
    expire_days = Settings().token_expire_days or DEFAULT_TOKEN_EXPIRE_DAYS
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + expire_days * 86400,
    }
    header = {"alg": "HS256", "typ": "JWT"}
    secret = _get_secret().encode("utf-8")
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_token(token: str) -> dict[str, Any] | None:
    """验证并解析 JWT，失败返回 None。"""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        return None
    secret = _get_secret().encode("utf-8")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected_sig, actual_sig):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """从 Authorization: Bearer <token> 解析当前用户。"""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = int(payload["sub"])
    user = await db.get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """要求当前用户为管理员。"""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


def is_admin(user: dict[str, Any]) -> bool:
    return user.get("role") == "admin"
