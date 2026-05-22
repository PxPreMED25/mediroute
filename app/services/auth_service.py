"""
인증 서비스 (STEP 4)
- JWT 토큰 생성/검증
- 카카오/네이버 OAuth 처리
- 사용자 조회/생성
"""

import logging
import httpx
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.database import get_db
from app.models.db_models import User

logger = logging.getLogger("mediroute.auth")

security = HTTPBearer(auto_error=False)

# JWT 설정
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7일


def create_access_token(user_id: int) -> str:
    """JWT 액세스 토큰 생성"""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[int]:
    """JWT 토큰 검증 → user_id 반환"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", 0))
        return user_id if user_id > 0 else None
    except JWTError:
        return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """
    현재 로그인 사용자 반환
    - 토큰 없으면 None (비로그인 허용 API용)
    - 토큰 잘못되면 401
    """
    if not credentials:
        return None

    user_id = verify_token(credentials.credentials)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 인증 토큰입니다.",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다.",
        )
    return user


async def require_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """로그인 필수 API용 — 비로그인 시 401"""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    return user


# ═══════════════════════════════════════
#  소셜 로그인: 카카오
# ═══════════════════════════════════════

async def kakao_get_user_info(access_token: str) -> Optional[dict]:
    """카카오 access_token → 사용자 정보 조회"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://kapi.kakao.com/v2/user/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            logger.warning(f"카카오 사용자 정보 실패: HTTP {resp.status_code}")
            return None

        data = resp.json()
        kakao_account = data.get("kakao_account", {})
        profile = kakao_account.get("profile", {})

        return {
            "provider": "kakao",
            "provider_id": str(data["id"]),
            "email": kakao_account.get("email", ""),
            "nickname": profile.get("nickname", ""),
            "profile_image": profile.get("profile_image_url", ""),
        }
    except Exception as e:
        logger.error(f"카카오 API 오류: {e}")
        return None


# ═══════════════════════════════════════
#  소셜 로그인: 네이버
# ═══════════════════════════════════════

async def naver_get_user_info(access_token: str) -> Optional[dict]:
    """네이버 access_token → 사용자 정보 조회"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://openapi.naver.com/v1/nid/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            return None

        data = resp.json().get("response", {})
        return {
            "provider": "naver",
            "provider_id": data.get("id", ""),
            "email": data.get("email", ""),
            "nickname": data.get("nickname", data.get("name", "")),
            "profile_image": data.get("profile_image", ""),
        }
    except Exception as e:
        logger.error(f"네이버 API 오류: {e}")
        return None


# ═══════════════════════════════════════
#  사용자 조회/생성
# ═══════════════════════════════════════

async def get_or_create_user(
    db: AsyncSession,
    provider: str,
    provider_id: str,
    email: str = "",
    nickname: str = "",
    profile_image: str = "",
) -> User:
    """소셜 로그인 후 사용자 조회 또는 신규 생성"""
    result = await db.execute(
        select(User).where(User.provider_id == provider_id)
    )
    user = result.scalar_one_or_none()

    if user:
        # 기존 사용자: 프로필 업데이트
        if nickname:
            user.nickname = nickname
        if profile_image:
            user.profile_image = profile_image
        if email:
            user.email = email
        await db.commit()
        logger.info(f"기존 사용자 로그인: {user.nickname} ({provider})")
        return user

    # 신규 사용자 생성
    user = User(
        provider=provider,
        provider_id=provider_id,
        email=email,
        nickname=nickname,
        profile_image=profile_image,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info(f"신규 사용자 생성: {user.nickname} ({provider}) id={user.id}")
    return user
