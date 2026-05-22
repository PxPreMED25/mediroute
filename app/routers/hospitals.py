"""
병원 검색 API 라우터 (STEP 2)
GET /api/hospitals — GPS 기반 실제 병원 검색
"""

import logging
from typing import Optional
from fastapi import APIRouter, Query
from app.services.hospital_service import (
    search_hospitals_hira,
    geocode_address,
    merge_and_fill_hospitals,
)

logger = logging.getLogger("mediroute.router.hospitals")

router = APIRouter(prefix="/api", tags=["병원 검색"])


@router.get(
    "/hospitals",
    summary="GPS 기반 주변 병원 검색",
    description="""
    심평원 병원정보서비스 API를 통해 실제 병원을 검색합니다.

    **검색 방법 (우선순위):**
    1. GPS 좌표 (lat, lng) — 반경 내 병원 검색
    2. 주소 텍스트 (address) — 카카오맵으로 좌표 변환 후 검색
    3. 지역명 (region) — 시도코드 기반 검색

    **필터:**
    - dept: 진료과명 (예: 정형외과, 내과)
    - inst_type: 의료기관 종별 (의원, 종합병원, 상급종합)
    - radius: 검색 반경 (미터, 기본 5000m)
    """,
)
async def search_hospitals(
    lat: Optional[float] = Query(None, description="위도 (GPS)"),
    lng: Optional[float] = Query(None, description="경도 (GPS)"),
    address: Optional[str] = Query(None, description="주소 텍스트 (좌표 없을 때)"),
    region: str = Query("", description="지역명 (예: 대전 서구)"),
    dept: str = Query("", description="진료과명 (예: 정형외과)"),
    inst_type: str = Query("", description="의원/종합병원/상급종합"),
    radius: int = Query(5000, description="검색 반경 (미터)", ge=500, le=50000),
    limit: int = Query(20, description="최대 결과 수", ge=1, le=50),
):
    user_lat = lat
    user_lng = lng

    # 좌표가 없으면 주소 또는 지역명으로 geocoding
    if not (user_lat and user_lng):
        geo_target = address or region
        if geo_target:
            coords = await geocode_address(geo_target)
            if coords:
                user_lat = coords["lat"]
                user_lng = coords["lng"]
                logger.info(f"Geocoded '{geo_target}' → {user_lat}, {user_lng}")

    real_hospitals = await search_hospitals_hira(
        region=region,
        dept_name=dept,
        inst_type=inst_type,
        user_lat=user_lat,
        user_lng=user_lng,
        radius_m=radius,
        num_of_rows=limit,
    )

    # 프론트와 동일한 병원명 리스트 구조로 보정한다.
    hospitals = merge_and_fill_hospitals(
        real_rows=real_hospitals,
        region=region or address or "현재 위치 주변",
        dept_names=[dept or "가정의학과"],
        per_type=5,
    )
    if inst_type:
        normalized_type = "대학병원" if "상급" in inst_type or "대학" in inst_type else "전문병원" if "병원" in inst_type and "의원" not in inst_type else "의원"
        hospitals = [h for h in hospitals if h["type"] == normalized_type]
    hospitals = hospitals[:limit]

    return {
        "count": len(hospitals),
        "query": {
            "lat": user_lat,
            "lng": user_lng,
            "region": region,
            "dept": dept,
            "inst_type": inst_type,
            "radius_m": radius,
        },
        "hospitals": hospitals,
    }
