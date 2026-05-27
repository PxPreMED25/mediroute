"""
병원 검색 API 라우터
GET /api/hospitals — GPS/지역/진료과 기반 병원 검색
"""

import logging
from typing import Optional
from fastapi import APIRouter, Query
from app.services.hospital_service import (
    search_hospitals_hira,
    search_hospitals_kakao_nearby,
    search_hospitals_kakao_region,
    search_hospitals_naver_nearby,
    geocode_address,
    build_fallback_hospitals,
    build_frontend_hospitals,
)

logger = logging.getLogger("mediroute.router.hospitals")

router = APIRouter(prefix="/api", tags=["병원 검색"])


@router.get(
    "/hospitals",
    summary="GPS 기반 주변 병원 검색",
    description="""
    provider=auto 기준으로 네이버 지역검색/지도 API를 우선 사용하고,
    네이버 결과가 없으면 카카오 Local API, 필요 시 HIRA 병원정보 API를 보조로 사용합니다.
    real_only=True이면 생성형 병원명을 채우지 않고 실제 API 결과만 반환합니다.
    """,
)
async def search_hospitals(
    lat: Optional[float] = Query(None, description="위도 (GPS)"),
    lng: Optional[float] = Query(None, description="경도 (GPS)"),
    address: Optional[str] = Query(None, description="주소 텍스트 (좌표 없을 때)"),
    region: str = Query("", description="지역명 (예: 대전 서구)"),
    dept: str = Query("", description="진료과명 (예: 정형외과)"),
    inst_type: str = Query("", description="의원/전문병원/대학병원"),
    radius: int = Query(5000, description="검색 반경 (미터)", ge=500, le=50000),
    limit: int = Query(20, description="최대 결과 수", ge=1, le=50),
    real_only: bool = Query(False, description="True이면 API 기반 실제 검색 결과만 반환하고 fallback 후보를 쓰지 않음"),
    provider: str = Query("auto", description="병원 검색 제공자: auto/naver/kakao/hira"),
):
    user_lat = lat
    user_lng = lng

    # 사용자가 GPS를 허용하지 않고 지역명을 입력한 경우에는
    # 지역명 + 진료과 키워드 검색을 우선 사용합니다.
    # 주소를 좌표로 바꿔 주변검색부터 하면 검색어에서 지역/진료과 맥락이 약해져
    # 실제 카카오맵 키워드 검색 결과와 다르게 비어 보일 수 있습니다.
    region_text = (region or address or "").strip()
    has_explicit_gps = bool(user_lat and user_lng)

    if not has_explicit_gps:
        geo_target = address or region
        if geo_target:
            coords = await geocode_address(geo_target)
            if coords:
                user_lat = coords["lat"]
                user_lng = coords["lng"]
                logger.info(f"Geocoded '{geo_target}' → {user_lat}, {user_lng}")

    hospitals = []

    # provider=auto 병원 검색 우선순위:
    # 1) 네이버 지역검색 + 네이버 지도 Geocoding/Directions
    # 2) 카카오 Local API
    # 3) HIRA 병원정보 API
    # real_only=True이면 생성형 병원명/fallback 후보를 채우지 않습니다.
    if inst_type:
        provider_norm = (provider or "auto").lower()
        if provider_norm in {"auto", "naver"}:
            hospitals = await search_hospitals_naver_nearby(
                dept_name=dept or "가정의학과",
                inst_type=inst_type,
                user_lat=user_lat,
                user_lng=user_lng,
                region=region or address or "",
                limit=limit,
            )

        if (not hospitals) and provider_norm in {"auto", "kakao"}:
            # GPS 없이 지역명을 입력한 경우: 카카오맵에서 사람이 검색하는 것과 동일하게
            # "지역명 + 진료과 + 의원/병원" 키워드 검색을 우선 수행합니다.
            if (not has_explicit_gps) and region_text:
                hospitals = await search_hospitals_kakao_region(
                    dept_name=dept or "가정의학과",
                    inst_type=inst_type,
                    region=region_text,
                    limit=limit,
                )
            # GPS를 직접 받은 경우 또는 지역 키워드 검색 결과가 없을 때만 좌표 주변 검색 보조
            if (not hospitals) and user_lat and user_lng:
                hospitals = await search_hospitals_kakao_nearby(
                    dept_name=dept or "가정의학과",
                    inst_type=inst_type,
                    user_lat=user_lat,
                    user_lng=user_lng,
                    radius_m=radius,
                    limit=limit,
                )

        if len(hospitals) < min(5, limit) and provider_norm in {"auto", "hira", "naver", "kakao"}:
            hira_inst_type = {"의원": "의원", "전문병원": "병원", "대학병원": "상급종합"}.get(inst_type, inst_type)
            hira = await search_hospitals_hira(
                region=region,
                dept_name=dept,
                inst_type=hira_inst_type,
                user_lat=user_lat,
                user_lng=user_lng,
                radius_m=radius,
                num_of_rows=limit,
            )
            hospitals.extend([{**h, "type": inst_type, "dept": dept or h.get("dept", ""), "openStatus": h.get("openStatus") or ""} for h in hira if h.get("name")])

        if not real_only and len(hospitals) < min(5, limit):
            hospitals.extend(build_fallback_hospitals(
                dept or "가정의학과",
                inst_type,
                region or address or "현재 위치 주변",
                min(5, limit) - len(hospitals),
                user_lat=user_lat,
                user_lng=user_lng,
            ))
    else:
        if real_only:
            hospitals = []
        else:
            hospitals = await build_frontend_hospitals(
                region=region or address or "현재 위치 주변",
                departments=[dept or "가정의학과"],
                user_lat=user_lat,
                user_lng=user_lng,
                limit_per_type=min(5, limit),
            )

    return {
        "count": len(hospitals),
        "query": {
            "lat": user_lat,
            "lng": user_lng,
            "region": region,
            "dept": dept,
            "inst_type": inst_type,
            "radius_m": radius,
            "real_only": real_only,
            "provider": provider,
            "source_note": "real_only=True이면 네이버/카카오/HIRA API 결과만 반환합니다. NAVER 또는 KAKAO API 키가 없으면 목록이 비어 있을 수 있습니다.",
        },
        "hospitals": hospitals[:limit],
    }
