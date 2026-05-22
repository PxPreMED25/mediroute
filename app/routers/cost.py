"""
진료비 & KCD 조회 API 라우터 (STEP 3)
GET /api/cost    — KCD 코드 기반 진료비 조회
GET /api/kcd     — 증상 텍스트 → KCD 코드 매핑
GET /api/kcd/{code} — KCD 코드 상세 정보
"""

import logging
from fastapi import APIRouter, Query, Path, HTTPException
from app.services.kcd_service import symptom_to_kcd, get_kcd_info, KCD_MASTER
from app.services.cost_service import get_comprehensive_cost

logger = logging.getLogger("mediroute.router.cost")

router = APIRouter(prefix="/api", tags=["진료비 & KCD"])


@router.get(
    "/kcd",
    summary="증상 → KCD 코드 변환",
    description="""
    사용자가 입력한 증상 텍스트를 분석하여
    매칭되는 KCD 질병 코드를 confidence 점수와 함께 반환합니다.

    예: "무릎이 아프고 계단 오를 때 힘들어요" → M17 (무릎관절증)
    """,
)
async def search_kcd(
    symptom: str = Query(..., description="증상 텍스트", min_length=2),
    top_n: int = Query(3, description="최대 결과 수", ge=1, le=10),
):
    results = symptom_to_kcd(symptom, top_n=top_n)

    return {
        "query": symptom,
        "count": len(results),
        "results": results,
    }


@router.get(
    "/kcd/{code}",
    summary="KCD 코드 상세 정보",
    description="KCD 코드로 질환명, 추천 진료과, 관련 키워드 등 상세 정보를 조회합니다.",
)
async def get_kcd_detail(
    code: str = Path(..., description="KCD 코드 (예: M17, K25)", min_length=2, max_length=10),
):
    info = get_kcd_info(code.upper())
    if not info:
        raise HTTPException(
            status_code=404,
            detail=f"KCD 코드 '{code}'에 대한 정보가 없습니다. 현재 근골격계/소화기계 주요 질환을 지원합니다.",
        )

    return info


@router.get(
    "/kcd-list",
    summary="지원 KCD 코드 전체 목록",
    description="현재 시스템에서 지원하는 KCD 코드 목록을 반환합니다.",
)
async def list_kcd_codes(
    category: str = Query("", description="카테고리 필터 (근골격계, 소화기계 등)"),
):
    items = []
    for code, entry in KCD_MASTER.items():
        if category and category not in entry.get("category", ""):
            continue
        items.append({
            "kcd": code,
            "name": entry["name"],
            "dept": entry["dept"],
            "category": entry.get("category", ""),
        })

    return {
        "count": len(items),
        "categories": list({e.get("category", "") for e in KCD_MASTER.values()}),
        "items": items,
    }


@router.get(
    "/cost",
    summary="KCD 기반 진료비 조회",
    description="""
    KCD 코드를 기반으로 예상 진료비를 조회합니다.

    **데이터 소스:**
    1. 심평원 질병정보서비스 API (실시간 통계)
    2. 비급여진료비정보조회서비스 (MRI, 수면내시경 등 실제 비급여 가격)
    3. 내장 KCD 마스터 테이블 (폴백)

    **예시:**
    - /api/cost?kcd=M17 → 무릎관절증 진료비
    - /api/cost?kcd=K25 → 위궤양 진료비
    - /api/cost?kcd=M17&region=대전 → 대전 지역 기준
    """,
)
async def get_cost(
    kcd: str = Query(..., description="KCD 코드 (예: M17)", min_length=2, max_length=10),
    region: str = Query("", description="지역 (향후 지역별 통계 반영)"),
    inst_type: str = Query("", description="의원/종합병원/상급종합"),
):
    result = await get_comprehensive_cost(
        kcd_code=kcd.upper(),
        region=region,
        inst_type=inst_type,
    )

    if not result.get("name"):
        raise HTTPException(
            status_code=404,
            detail=f"KCD 코드 '{kcd}'에 대한 비용 정보가 없습니다.",
        )

    return result
