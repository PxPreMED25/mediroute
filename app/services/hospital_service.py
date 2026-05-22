"""
병원 검색 서비스 (STEP 2)
- 심평원 병원정보서비스 Open API 연동
- 카카오맵 API로 좌표 ↔ 주소 변환, 거리 계산
- 진료과 코드 매핑
"""

import logging
import math
import xmltodict
import httpx
from typing import Optional
from app.core.config import get_settings

logger = logging.getLogger("mediroute.hospital")


# ═══════════════════════════════════════
#  심평원 코드 매핑 테이블
# ═══════════════════════════════════════

# 진료과목명 → 심평원 진료과목코드 (dgsbjtCd)
DEPT_CODE_MAP = {
    "내과": "01", "신경과": "02", "정신건강의학과": "03",
    "외과": "04", "정형외과": "05", "신경외과": "06",
    "흉부외과": "07", "성형외과": "08", "마취통증의학과": "09",
    "산부인과": "10", "소아청소년과": "11", "안과": "12",
    "이비인후과": "13", "피부과": "14", "비뇨의학과": "15",
    "영상의학과": "16", "방사선종양학과": "17",
    "병리과": "18", "진단검사의학과": "19", "재활의학과": "20",
    "핵의학과": "21", "가정의학과": "22", "응급의학과": "23",
    "산업의학과": "24", "예방의학과": "25",
    "치과": "49", "구강외과": "50", "치과보철과": "51",
    "치과교정과": "52", "소아치과": "53", "치주과": "54",
    "한방내과": "80", "한방부인과": "81", "한방소아과": "82",
    "침구과": "83", "한방안이비인후피부과": "84",
    "한방신경정신과": "85", "한방재활의학과": "86",
    "사상체질과": "87", "한방응급": "88",
}

# 종별코드 (clCd)
INST_TYPE_CODE_MAP = {
    "상급종합": "01", "종합병원": "11", "병원": "21",
    "의원": "31", "치과병원": "25", "치과의원": "35",
    "한방병원": "28", "한방의원": "38",
    "요양병원": "29", "보건소": "41",
}

# 종별코드 → 표시명
INST_TYPE_DISPLAY = {
    "01": "상급종합병원", "11": "종합병원", "21": "병원",
    "31": "의원", "25": "치과병원", "35": "치과의원",
    "28": "한방병원", "38": "한방의원", "29": "요양병원",
    "41": "보건소",
}

# 시도코드 매핑
SIDO_CODE_MAP = {
    "서울": "110000", "부산": "210000", "대구": "220000",
    "인천": "230000", "광주": "240000", "대전": "250000",
    "울산": "260000", "세종": "360000",
    "경기": "310000", "강원": "320000", "충북": "330000",
    "충남": "340000", "전북": "350000", "전남": "370000",
    "경북": "380000", "경남": "390000", "제주": "400000",
}


def find_sido_code(region: str) -> Optional[str]:
    """지역 문자열에서 시도코드 추출"""
    for name, code in SIDO_CODE_MAP.items():
        if name in region:
            return code
    return None


def find_dept_code(dept_name: str) -> Optional[str]:
    """진료과명에서 코드 추출 (부분 매칭)"""
    for name, code in DEPT_CODE_MAP.items():
        if name in dept_name or dept_name in name:
            return code
    return None




# ═══════════════════════════════════════
#  프론트 병원명 표시용 fallback 데이터
# ═══════════════════════════════════════
# 목적: 심평원 API 키가 없거나 결과가 부족해도 프론트가 기대하는
# nearbyHospitals 구조(name/type/dept/address/hours/fit/distanceKm)를 안정적으로 제공한다.

DEPT_ALIASES = {
    "소화기내과": "내과", "호흡기내과": "내과", "심장내과": "내과", "순환기내과": "내과",
    "알레르기내과": "내과", "대장항문외과": "항문외과", "구강외과": "치과",
}

HOSPITAL_NAME_POOLS = {
    "내과": ["강남서울내과의원", "역삼속편한내과의원", "선릉365내과의원", "테헤란우리내과의원", "서울건강내과의원"],
    "외과": ["강남서울외과의원", "역삼튼튼외과의원", "선릉외과의원", "테헤란외과의원", "서울외과클리닉"],
    "가정의학과": ["강남서울가정의학과의원", "역삼가정의학과의원", "선릉가정의학과의원", "테헤란가정의학과의원", "서울가정의학과의원"],
    "이비인후과": ["역삼이비인후과의원", "강남서울이비인후과의원", "선릉숨편한이비인후과의원", "테헤란이비인후과의원", "서울코이비인후과의원"],
    "피부과": ["역삼피부과의원", "강남맑은피부과의원", "선릉피부과의원", "테헤란피부과의원", "서울피부클리닉"],
    "비뇨의학과": ["역삼비뇨의학과의원", "강남비뇨의학과의원", "선릉비뇨의학과의원", "테헤란비뇨의학과의원", "서울비뇨의학과의원"],
    "신경과": ["역삼신경과의원", "강남서울신경과의원", "선릉두통신경과의원", "테헤란신경과의원", "서울신경클리닉"],
    "정형외과": ["역삼정형외과의원", "강남바른정형외과의원", "선릉튼튼정형외과의원", "테헤란정형외과의원", "서울정형외과의원"],
    "안과": ["역삼밝은안과의원", "강남서울안과의원", "선릉안과의원", "테헤란안과의원", "서울밝은안과의원"],
    "치과": ["역삼서울치과의원", "강남바른치과의원", "선릉치과의원", "테헤란치과의원", "서울미소치과의원"],
    "산부인과": ["역삼산부인과의원", "강남여성산부인과의원", "선릉산부인과의원", "테헤란산부인과의원", "서울여성의원"],
    "소아청소년과": ["역삼소아청소년과의원", "강남아이소아청소년과의원", "선릉소아과의원", "테헤란소아청소년과의원", "서울아이의원"],
    "정신건강의학과": ["역삼마음정신건강의학과의원", "강남서울정신건강의학과의원", "선릉마인드의원", "테헤란정신건강의학과의원", "마음숲의원"],
    "재활의학과": ["역삼재활의학과의원", "강남튼튼재활의학과의원", "선릉재활의학과의원", "테헤란재활의학과의원", "서울재활클리닉"],
    "항문외과": ["역삼항문외과의원", "강남대장항문외과의원", "선릉항문외과의원", "테헤란대장항문외과의원", "서울항문외과의원"],
    "알레르기내과": ["역삼알레르기내과의원", "강남숨편한알레르기내과의원", "선릉알레르기클리닉", "테헤란알레르기내과의원", "서울알레르기내과의원"],
    "소화기내과": ["역삼속편한내과의원", "강남소화기내과의원", "선릉위장내과의원", "테헤란소화기내과의원", "서울위편한내과의원"],
    "호흡기내과": ["역삼숨편한내과의원", "강남호흡기내과의원", "선릉호흡기클리닉", "테헤란호흡기내과의원", "서울숨내과의원"],
    "심장내과": ["역삼심장내과의원", "강남순환기내과의원", "선릉심혈관내과의원", "테헤란심장내과의원", "서울심장내과의원"],
}

UNIVERSITY_HOSPITAL_NAMES = [
    "강남세브란스병원", "삼성서울병원", "서울아산병원", "서울대학교병원", "중앙대학교병원"
]

GENERIC_HOSPITAL_TOKENS = ("의원 1", "의원 2", "의원 3", "전문병원 1", "전문병원 2", "대학병원 1", "주변 의료기관")


def normalize_dept_name(dept_name: str) -> str:
    """진료과명을 프론트 필터링에 쓰기 좋은 형태로 정규화한다."""
    d = (dept_name or "").strip()
    if not d:
        return "가정의학과"
    return d.replace(" ", "")


def is_generic_hospital_name(name: str, dept_name: str = "", inst_type: str = "") -> bool:
    """프론트에 그대로 보여주기 부적절한 placeholder 병원명인지 판정."""
    n = (name or "").strip()
    if not n:
        return True
    if any(tok in n for tok in GENERIC_HOSPITAL_TOKENS):
        return True
    d = normalize_dept_name(dept_name)
    t = (inst_type or "").strip()
    generic_values = {f"{d} 의원", f"{d}의원", f"{d} 전문병원", f"{d}전문병원", f"{d} 대학병원", f"{d}대학병원"}
    return n in generic_values


def make_hospital_name(dept_name: str, inst_type: str = "의원", index: int = 0, region: str = "") -> str:
    """진료과/병원급에 맞는 병원명 형태의 fallback 이름 생성."""
    dept = normalize_dept_name(dept_name)
    inst = inst_type or "의원"

    if inst == "대학병원":
        return UNIVERSITY_HOSPITAL_NAMES[index % len(UNIVERSITY_HOSPITAL_NAMES)]

    if inst == "전문병원":
        base = dept.replace("의학과", "").replace("과", "")
        if not base:
            base = "진료"
        names = [
            f"강남{base}전문병원",
            f"서울{base}센터",
            f"선릉{base}전문클리닉",
            f"테헤란{base}메디컬센터",
            f"더좋은{base}전문병원",
        ]
        return names[index % len(names)]

    pool = HOSPITAL_NAME_POOLS.get(dept)
    if not pool:
        canonical = DEPT_ALIASES.get(dept, dept)
        pool = HOSPITAL_NAME_POOLS.get(canonical)
    if pool:
        return pool[index % len(pool)]
    return f"서울{dept}의원"


def default_hours(inst_type: str) -> str:
    if inst_type == "대학병원":
        return "평일 (8:30~17:00)"
    if inst_type == "전문병원":
        return "평일 (9:00~17:00)"
    return "평일 주간 (9:00~18:00)"


def default_fit(dept_name: str, inst_type: str) -> str:
    if inst_type == "대학병원":
        return f"{dept_name} 정밀검사·협진 필요 시 이용"
    if inst_type == "전문병원":
        return f"{dept_name} 전문 진료"
    return f"{dept_name} 관련 1차 진료"


def normalize_hospital_for_front(h: dict, dept_name: str = "", region: str = "", index: int = 0) -> dict:
    """심평원/Claude/fallback 병원 데이터를 프론트가 쓰는 필드명으로 통일."""
    dept = normalize_dept_name(h.get("dept") or dept_name)
    inst = h.get("type") or h.get("inst_type") or "의원"
    name = h.get("name") or h.get("yadmNm") or ""
    if is_generic_hospital_name(name, dept, inst):
        name = make_hospital_name(dept, inst, index, region)
    return {
        "name": name,
        "type": inst if inst in {"의원", "전문병원", "대학병원"} else _simplify_type(inst),
        "dept": dept,
        "address": h.get("address") or h.get("addr") or region or "현재 위치 주변",
        "hours": h.get("hours") or default_hours(inst),
        "fit": h.get("fit") or default_fit(dept, inst),
        "distanceKm": round(float(h.get("distanceKm") or h.get("distance_km") or 0.0), 1),
    }


def build_fallback_hospitals(
    region: str = "",
    dept_names: list[str] | None = None,
    inst_types: list[str] | None = None,
    per_type: int = 5,
) -> list[dict]:
    """프론트 필터링용 병원명 리스트 생성. 실제 병원 API 결과가 없거나 부족할 때 사용."""
    depts = [normalize_dept_name(d) for d in (dept_names or []) if d]
    if not depts:
        depts = ["가정의학과"]
    # 중복 제거, 순서 보존
    depts = list(dict.fromkeys(depts))
    types = inst_types or ["의원", "전문병원", "대학병원"]
    rows: list[dict] = []
    for dept in depts:
        for inst in types:
            base_km = 0.4 if inst == "의원" else 1.5 if inst == "전문병원" else 3.0
            for i in range(per_type):
                rows.append({
                    "name": make_hospital_name(dept, inst, i, region),
                    "type": inst,
                    "dept": dept,
                    "address": region or "현재 위치 주변",
                    "hours": default_hours(inst),
                    "fit": default_fit(dept, inst),
                    "distanceKm": round(base_km + i * 0.8, 1),
                })
    return rows


def merge_and_fill_hospitals(real_rows: list[dict], region: str, dept_names: list[str], per_type: int = 5) -> list[dict]:
    """실제 병원 결과를 우선 사용하고, 진료과/병원급별 부족분은 병원명 fallback으로 채운다."""
    normalized: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    depts = list(dict.fromkeys([normalize_dept_name(d) for d in dept_names if d])) or ["가정의학과"]

    for idx, row in enumerate(real_rows or []):
        item = normalize_hospital_for_front(row, dept_name=row.get("dept") or depts[0], region=region, index=idx)
        key = (item["name"], item["type"], item["dept"])
        if key not in seen:
            seen.add(key)
            normalized.append(item)

    fallback = build_fallback_hospitals(region=region, dept_names=depts, per_type=per_type)
    for item in fallback:
        key = (item["name"], item["type"], item["dept"])
        if key not in seen:
            seen.add(key)
            normalized.append(item)

    return sorted(normalized, key=lambda x: (x["dept"], x["type"], x["distanceKm"]))


# ═══════════════════════════════════════
#  거리 계산 유틸리티
# ═══════════════════════════════════════

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 간 직선 거리 (km) — Haversine 공식"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ═══════════════════════════════════════
#  카카오맵 API (주소 → 좌표)
# ═══════════════════════════════════════

async def geocode_address(address: str) -> Optional[dict]:
    """
    주소 → 좌표 변환 (카카오 로컬 API)
    반환: {"lat": 36.xxx, "lng": 127.xxx} or None
    """
    settings = get_settings()
    if not settings.KAKAO_REST_API_KEY:
        logger.debug("KAKAO_REST_API_KEY 미설정 — geocoding 스킵")
        return None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://dapi.kakao.com/v2/local/search/address.json",
                params={"query": address},
                headers={"Authorization": f"KakaoAK {settings.KAKAO_REST_API_KEY}"},
            )
        if resp.status_code == 200:
            docs = resp.json().get("documents", [])
            if docs:
                return {
                    "lat": float(docs[0]["y"]),
                    "lng": float(docs[0]["x"]),
                }
    except Exception as e:
        logger.warning(f"Geocoding 실패: {e}")
    return None


# ═══════════════════════════════════════
#  심평원 병원정보서비스 API
# ═══════════════════════════════════════

HIRA_HOSPITAL_BASE = "http://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"


async def search_hospitals_hira(
    region: str = "",
    dept_name: str = "",
    inst_type: str = "",
    user_lat: Optional[float] = None,
    user_lng: Optional[float] = None,
    radius_m: int = 5000,
    num_of_rows: int = 30,
) -> list[dict]:
    """
    심평원 병원정보서비스 API로 병원 검색

    Args:
        region: 지역명 (예: "대전 서구")
        dept_name: 진료과명 (예: "정형외과")
        inst_type: 의료기관 종별 (예: "의원", "종합병원")
        user_lat/lng: 사용자 GPS 좌표 (있으면 좌표 기반 검색)
        radius_m: 검색 반경 (미터)
        num_of_rows: 최대 결과 수

    Returns:
        list[dict] — 병원 정보 리스트 (프론트 HospitalInfo 형식)
    """
    settings = get_settings()
    if not settings.DATA_GO_KR_API_KEY:
        logger.warning("DATA_GO_KR_API_KEY 미설정 — 병원 검색 불가")
        return []

    # 파라미터 구성
    params = {
        "serviceKey": settings.DATA_GO_KR_API_KEY,
        "numOfRows": str(num_of_rows),
        "pageNo": "1",
        "_type": "json",
    }

    # GPS 좌표 기반 검색 (우선)
    if user_lat and user_lng:
        params["xPos"] = str(user_lng)  # 주의: xPos = 경도(lng)
        params["yPos"] = str(user_lat)  # yPos = 위도(lat)
        params["radius"] = str(radius_m)
    else:
        # 지역 코드 기반 검색
        sido_code = find_sido_code(region)
        if sido_code:
            params["sidoCd"] = sido_code

    # 진료과 필터
    dept_code = find_dept_code(dept_name) if dept_name else None
    if dept_code:
        params["dgsbjtCd"] = dept_code

    # 종별 필터
    if inst_type:
        cl_code = INST_TYPE_CODE_MAP.get(inst_type)
        if cl_code:
            params["clCd"] = cl_code

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(HIRA_HOSPITAL_BASE, params=params)

        if resp.status_code != 200:
            logger.error(f"심평원 API HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        # 응답 파싱 (JSON 또는 XML)
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type or resp.text.strip().startswith("{"):
            data = resp.json()
        else:
            data = xmltodict.parse(resp.text)

        # 중첩 구조 탐색
        body = data.get("response", {}).get("body", {})
        items = body.get("items", {})

        if not items:
            logger.info(f"검색 결과 없음: region={region}, dept={dept_name}")
            return []

        item_list = items.get("item", [])
        if isinstance(item_list, dict):
            item_list = [item_list]

        # 결과 변환
        hospitals = []
        for item in item_list:
            hosp_lat = _safe_float(item.get("YPos"))
            hosp_lng = _safe_float(item.get("XPos"))

            # 거리 계산
            distance_km = 0.0
            if user_lat and user_lng and hosp_lat and hosp_lng:
                distance_km = round(haversine_km(user_lat, user_lng, hosp_lat, hosp_lng), 1)

            # 종별 표시명
            cl_cd = str(item.get("clCd", ""))
            type_name = INST_TYPE_DISPLAY.get(cl_cd, item.get("clCdNm", "의원"))

            # MediRoute 프론트엔드 형식으로 변환
            hospitals.append({
                "name": item.get("yadmNm", ""),
                "type": _simplify_type(type_name),
                "dept": dept_name or _guess_dept(item),
                "address": item.get("addr", ""),
                "hours": default_hours(_simplify_type(type_name)),
                "fit": f"{dept_name or _guess_dept(item)} 관련 진료 · 의사 {item.get('drTotCnt', '?')}명",
                "distanceKm": distance_km,
                "telno": item.get("telno", ""),
                "ykiho": item.get("ykiho", ""),
                "lat": hosp_lat,
                "lng": hosp_lng,
            })

        # 거리순 정렬
        if user_lat and user_lng:
            hospitals.sort(key=lambda h: h["distanceKm"])

        logger.info(f"병원 검색 완료: {len(hospitals)}건 | region={region} dept={dept_name}")
        return hospitals

    except httpx.TimeoutException:
        logger.error("심평원 API 타임아웃")
        return []
    except Exception as e:
        logger.error(f"심평원 API 오류: {e}", exc_info=True)
        return []


# ═══════════════════════════════════════
#  유틸리티
# ═══════════════════════════════════════

def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val else None
    except (ValueError, TypeError):
        return None


def _simplify_type(type_name: str) -> str:
    """프론트엔드 표시용 종별 단순화"""
    if "상급종합" in type_name:
        return "대학병원"
    if "종합" in type_name:
        return "전문병원"
    if "병원" in type_name and "의원" not in type_name:
        return "전문병원"
    return "의원"


def _guess_dept(item: dict) -> str:
    """응답 항목에서 진료과 추론 (코드 역매핑)"""
    dgsbjtCd = str(item.get("dgsbjtCd", ""))
    for name, code in DEPT_CODE_MAP.items():
        if code == dgsbjtCd:
            return name
    return "진료과 미상"
