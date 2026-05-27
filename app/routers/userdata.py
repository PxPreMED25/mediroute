"""
사용자 데이터 API 라우터 (STEP 4)
증상 이력, 가족 구성원, 즐겨찾기 병원 CRUD
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.db_models import User, FamilyMember, SymptomHistory, FavoriteHospital
from app.services.auth_service import require_user

logger = logging.getLogger("mediroute.router.userdata")

router = APIRouter(prefix="/api/user", tags=["사용자 데이터"])


# ═══════════════════════════════════════
#  증상 이력
# ═══════════════════════════════════════

class SaveHistoryRequest(BaseModel):
    symptom: str = ""
    areas: list[str] = []
    age: str = ""
    gender: str = ""
    region: str = ""
    duration: str = ""
    meds: str = ""
    disease: str = ""
    is_urgent: bool = False
    predicted_diseases: list[dict] = []
    recommended_depts: list[str] = []
    urgency_text: str = "낮음"
    kcd_code: str = ""


@router.post(
    "/history",
    summary="증상 분석 이력 저장",
    description="분석 결과를 사용자 이력으로 저장합니다. 로그인 필요.",
)
async def save_history(
    req: SaveHistoryRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    history = SymptomHistory(
        user_id=user.id,
        symptom=req.symptom,
        areas=req.areas,
        age=req.age,
        gender=req.gender,
        region=req.region,
        duration=req.duration,
        meds=req.meds,
        disease=req.disease,
        is_urgent=req.is_urgent,
        predicted_diseases=req.predicted_diseases,
        recommended_depts=req.recommended_depts,
        urgency_text=req.urgency_text,
        kcd_code=req.kcd_code,
    )
    db.add(history)
    await db.commit()
    await db.refresh(history)

    return {"id": history.id, "message": "이력이 저장되었습니다."}


@router.get(
    "/history",
    summary="증상 분석 이력 조회",
    description="최근 분석 이력을 최신순으로 반환합니다.",
)
async def get_history(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SymptomHistory)
        .where(SymptomHistory.user_id == user.id)
        .order_by(desc(SymptomHistory.created_at))
        .limit(limit)
    )
    histories = result.scalars().all()

    return {
        "count": len(histories),
        "items": [
            {
                "id": h.id,
                "symptom": h.symptom,
                "areas": h.areas,
                "region": h.region,
                "predicted_diseases": h.predicted_diseases,
                "recommended_depts": h.recommended_depts,
                "urgency_text": h.urgency_text,
                "kcd_code": h.kcd_code,
                "created_at": h.created_at.isoformat() if h.created_at else "",
            }
            for h in histories
        ],
    }


@router.delete(
    "/history/{history_id}",
    summary="증상 이력 삭제",
)
async def delete_history(
    history_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SymptomHistory).where(
            SymptomHistory.id == history_id,
            SymptomHistory.user_id == user.id,
        )
    )
    history = result.scalar_one_or_none()
    if not history:
        raise HTTPException(status_code=404, detail="이력을 찾을 수 없습니다.")

    await db.delete(history)
    await db.commit()
    return {"message": "삭제되었습니다."}


# ═══════════════════════════════════════
#  가족 구성원
# ═══════════════════════════════════════

class FamilyMemberRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    relation: str = ""    # 본인/자녀/배우자/부모
    birth_year: int = 0
    gender: str = ""
    disease: str = ""
    meds: str = ""


@router.post(
    "/family",
    summary="가족 구성원 추가",
    description="자녀, 배우자 등 가족 프로필을 등록합니다. 증상 입력 시 나이/성별 자동 반영.",
)
async def add_family_member(
    req: FamilyMemberRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    member = FamilyMember(
        user_id=user.id,
        name=req.name,
        relation=req.relation,
        birth_year=req.birth_year,
        gender=req.gender,
        disease=req.disease,
        meds=req.meds,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)

    return {"id": member.id, "message": f"{req.name}님이 등록되었습니다."}


@router.get(
    "/family",
    summary="가족 구성원 목록",
)
async def get_family_members(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FamilyMember).where(FamilyMember.user_id == user.id)
    )
    members = result.scalars().all()

    return {
        "count": len(members),
        "items": [
            {
                "id": m.id,
                "name": m.name,
                "relation": m.relation,
                "birth_year": m.birth_year,
                "gender": m.gender,
                "disease": m.disease,
                "meds": m.meds,
            }
            for m in members
        ],
    }


@router.delete(
    "/family/{member_id}",
    summary="가족 구성원 삭제",
)
async def delete_family_member(
    member_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FamilyMember).where(
            FamilyMember.id == member_id,
            FamilyMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="가족 구성원을 찾을 수 없습니다.")

    await db.delete(member)
    await db.commit()
    return {"message": "삭제되었습니다."}


# ═══════════════════════════════════════
#  즐겨찾기 병원
# ═══════════════════════════════════════

class FavoriteHospitalRequest(BaseModel):
    hospital_name: str = Field(..., min_length=1)
    hospital_address: str = ""
    hospital_type: str = ""
    dept: str = ""
    ykiho: str = ""
    lat: float = 0.0
    lng: float = 0.0
    memo: str = ""


@router.post(
    "/favorites",
    summary="병원 즐겨찾기 추가",
)
async def add_favorite(
    req: FavoriteHospitalRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    fav = FavoriteHospital(
        user_id=user.id,
        hospital_name=req.hospital_name,
        hospital_address=req.hospital_address,
        hospital_type=req.hospital_type,
        dept=req.dept,
        ykiho=req.ykiho,
        lat=req.lat,
        lng=req.lng,
        memo=req.memo,
    )
    db.add(fav)
    await db.commit()
    await db.refresh(fav)

    return {"id": fav.id, "message": "즐겨찾기에 추가되었습니다."}


@router.get(
    "/favorites",
    summary="즐겨찾기 병원 목록",
)
async def get_favorites(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FavoriteHospital)
        .where(FavoriteHospital.user_id == user.id)
        .order_by(desc(FavoriteHospital.created_at))
    )
    favs = result.scalars().all()

    return {
        "count": len(favs),
        "items": [
            {
                "id": f.id,
                "hospital_name": f.hospital_name,
                "hospital_address": f.hospital_address,
                "hospital_type": f.hospital_type,
                "dept": f.dept,
                "lat": f.lat,
                "lng": f.lng,
                "memo": f.memo,
                "created_at": f.created_at.isoformat() if f.created_at else "",
            }
            for f in favs
        ],
    }


@router.delete(
    "/favorites/{fav_id}",
    summary="즐겨찾기 삭제",
)
async def delete_favorite(
    fav_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FavoriteHospital).where(
            FavoriteHospital.id == fav_id,
            FavoriteHospital.user_id == user.id,
        )
    )
    fav = result.scalar_one_or_none()
    if not fav:
        raise HTTPException(status_code=404, detail="즐겨찾기를 찾을 수 없습니다.")

    await db.delete(fav)
    await db.commit()
    return {"message": "삭제되었습니다."}
