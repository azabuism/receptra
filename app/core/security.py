"""
セキュリティユーティリティ - パスワードハッシング、JWT トークン処理
"""

from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# パスワードハッシング設定 - argon2 を使用（bcrypt 互換性問題を回避）
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


class TokenData(BaseModel):
    """JWT トークンペイロード"""
    sub: str  # ユーザーID
    tenant_id: str
    exp: Optional[datetime] = None


def hash_password(password: str) -> str:
    """パスワードをハッシュ化"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """パスワードの検証"""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    user_id: str,
    tenant_id: str,
    secret_key: str,
    algorithm: str = "HS256",
    expires_in_hours: int = 24,
) -> str:
    """
    JWT アクセストークンを生成

    Args:
        user_id: ユーザーID
        tenant_id: テナントID
        secret_key: 秘密鍵
        algorithm: 暗号化アルゴリズム
        expires_in_hours: 有効期限（時間）

    Returns:
        JWT トークン文字列
    """
    expire = datetime.utcnow() + timedelta(hours=expires_in_hours)

    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "exp": expire,
    }

    encoded_jwt = jwt.encode(payload, secret_key, algorithm=algorithm)
    return encoded_jwt


def verify_token(
    token: str,
    secret_key: str,
    algorithm: str = "HS256",
) -> Optional[TokenData]:
    """
    JWT トークンを検証してデータを抽出

    Args:
        token: JWT トークン文字列
        secret_key: 秘密鍵
        algorithm: 暗号化アルゴリズム

    Returns:
        TokenData オブジェクト、または None（検証失敗時）
    """
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
        user_id = payload.get("sub")
        tenant_id = payload.get("tenant_id")

        if user_id is None or tenant_id is None:
            return None

        return TokenData(sub=user_id, tenant_id=tenant_id)
    except JWTError:
        return None
