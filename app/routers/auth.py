"""
인증 API 라우터 (STEP 4)
POST /api/auth/kakao   — 카카오 소셜 로그인
POST /api/auth/naver   — 네이버 소셜 로그인
GET  /api/auth/me      — 현재 사용자 정보
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.db_models import User
from app.services.auth_service import (
    kakao_get_user_info,
    naver_get_user_info,
    get_or_create_user,
    create_access_token,
    require_user,
)

logger = logging.getLogger("mediroute.router.auth")

router = APIRouter(prefix="/api/auth", tags=["인증"])


class SocialLoginRequest(BaseModel):
    access_token: str


class AuthResponse(BaseModel):
    token: str
    user: dict


@router.post(
    "/kakao",
    summary="카카오 소셜 로그인",
    description="""
    프론트엔드에서 카카오 SDK로 받은 access_token을 전달하면,
    서버에서 카카오 사용자 정보를 조회하고 JWT 토큰을 발급합니다.

    **프론트 흐름:**
    1. Kakao.Auth.login() → access_token 획득
    2. POST /api/auth/kakao { access_token: "..." }
    3. 응답의 token을 localStorage에 저장
    4. 이후 요청 시 Authorization: Bearer {token} 헤더 추가
    """,
)
async def login_kakao(
    req: SocialLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    info = await kakao_get_user_info(req.access_token)
    if not info:
        raise HTTPException(status_code=401, detail="카카오 인증에 실패했습니다.")

    user = await get_or_create_user(
        db,
        provider=info["provider"],
        provider_id=info["provider_id"],
        email=info.get("email", ""),
        nickname=info.get("nickname", ""),
        profile_image=info.get("profile_image", ""),
    )

    token = create_access_token(user.id)
    return {
        "token": token,
        "user": {
            "id": user.id,
            "nickname": user.nickname,
            "email": user.email,
            "profile_image": user.profile_image,
            "provider": user.provider,
        },
    }


@router.post(
    "/naver",
    summary="네이버 소셜 로그인",
    description="프론트엔드에서 네이버 SDK로 받은 access_token을 전달합니다.",
)
async def login_naver(
    req: SocialLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    info = await naver_get_user_info(req.access_token)
    if not info:
        raise HTTPException(status_code=401, detail="네이버 인증에 실패했습니다.")

    user = await get_or_create_user(
        db,
        provider=info["provider"],
        provider_id=info["provider_id"],
        email=info.get("email", ""),
        nickname=info.get("nickname", ""),
        profile_image=info.get("profile_image", ""),
    )

    token = create_access_token(user.id)
    return {
        "token": token,
        "user": {
            "id": user.id,
            "nickname": user.nickname,
            "email": user.email,
            "profile_image": user.profile_image,
            "provider": user.provider,
        },
    }


@router.get(
    "/me",
    summary="현재 사용자 정보",
    description="JWT 토큰으로 로그인한 사용자의 프로필 정보를 반환합니다.",
)
async def get_me(user: User = Depends(require_user)):
    return {
        "id": user.id,
        "nickname": user.nickname,
        "email": user.email,
        "profile_image": user.profile_image,
        "provider": user.provider,
        "default_region": user.default_region,
        "created_at": user.created_at.isoformat() if user.created_at else "",
    }
