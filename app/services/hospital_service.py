"""
병원 검색 서비스 (STEP 2)
- 심평원 병원정보서비스 Open API 연동
- 카카오맵 API로 좌표 ↔ 주소 변환, 거리 계산
- 진료과 코드 매핑
"""

import logging
import math
try:
    import xmltodict
except Exception:  # 로컬 테스트/배포 환경에서 패키지가 빠진 경우 방어
    xmltodict = None
import httpx
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
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
#  카카오 로컬 API — 현재 좌표 기준 실제 병원/의원 검색
# ═══════════════════════════════════════

def _dept_search_terms(dept_name: str, inst_type: str) -> list[str]:
    """진료과/병원급에 맞는 카카오맵 장소 검색어 후보."""
    dept = normalize_dept_name(dept_name or DEFAULT_DEPT)
    stem = dept.replace("의학과", "").replace("건강", "").strip()
    aliases = {
        "비뇨의학과": ["비뇨의학과", "비뇨기과"],
        "이비인후과": ["이비인후과"],
        "정신건강의학과": ["정신건강의학과", "정신과"],
        "산부인과": ["산부인과", "여성의원"],
        "대장항문외과": ["항문외과", "대장항문외과"],
        "치과": ["치과"],
        "내과": ["내과"],
        "피부과": ["피부과"],
        "정형외과": ["정형외과"],
    }.get(dept, [dept, stem])
    aliases = [a for i, a in enumerate(aliases) if a and a not in aliases[:i]]
    if inst_type == "대학병원":
        return [q for a in aliases for q in [f"대학병원 {a}", f"상급종합병원 {a}", f"종합병원 {a}"]]
    if inst_type == "전문병원":
        return [q for a in aliases for q in [f"{a} 병원", f"전문병원 {a}"]]
    return [q for a in aliases for q in [f"{a} 의원", a]]


def _is_probably_medical_place(name: str, category: str) -> bool:
    text = f"{name or ''} {category or ''}"
    return any(k in text for k in ["병원", "의원", "클리닉", "의료원", "치과", "한의원", "보건"])


def _is_relevant_place(name: str, category: str, dept_name: str, inst_type: str) -> bool:
    """카카오 장소 검색 결과에서 진료과/병원급 불일치 결과를 줄임."""
    if not _is_probably_medical_place(name, category):
        return False
    dept = normalize_dept_name(dept_name or "")
    stem = dept.replace("의학과", "").replace("과", "")
    text = f"{name or ''} {category or ''}"
    # 대학병원/종합병원은 진료과명이 장소명에 없을 수 있어 병원급만 맞으면 허용
    if inst_type == "대학병원":
        return any(k in text for k in ["대학병원", "상급종합", "종합병원", "의료원", "병원"])
    # 전문병원은 의원 결과를 가능한 배제
    if inst_type == "전문병원" and "의원" in name and "병원" not in name:
        return False
    if dept and (dept in text or stem in text):
        return True
    # 일부 전문과 별칭
    aliases = {
        "정형외과": ["정형", "관절", "척추", "통증"],
        "이비인후과": ["이비인후", "귀", "코", "목"],
        "비뇨의학과": ["비뇨", "남성"],
        "피부과": ["피부"],
        "내과": ["내과", "속편한", "소화기", "호흡기"],
        "산부인과": ["산부인", "여성", "미즈"],
        "안과": ["안과", "눈"],
        "치과": ["치과"],
    }
    return any(a in text for a in aliases.get(dept, []))


async def search_hospitals_kakao_nearby(
    dept_name: str = "",
    inst_type: str = "의원",
    user_lat: Optional[float] = None,
    user_lng: Optional[float] = None,
    radius_m: int = 7000,
    limit: int = 20,
) -> list[dict]:
    """
    카카오 로컬 API로 현재 좌표 주변의 실제 의료기관을 거리순 검색.
    - 브라우저 GPS 좌표가 있어야 정확한 거리순 정렬 가능
    - KAKAO_REST_API_KEY 환경변수 필요
    - 실시간 영업 여부는 카카오/네이버 Local 검색 응답에 포함되지 않아 지도에서 최종 확인
    """
    settings = get_settings()
    if not settings.KAKAO_REST_API_KEY or not (user_lat and user_lng):
        return []

    headers = {"Authorization": f"KakaoAK {settings.KAKAO_REST_API_KEY}"}
    terms = _dept_search_terms(dept_name, inst_type)
    found: list[dict] = []
    seen: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for query in terms:
                for page in range(1, 4):  # 최대 45개 후보까지 확인
                    resp = await client.get(
                        "https://dapi.kakao.com/v2/local/search/keyword.json",
                        params={
                            "query": query,
                            "x": str(user_lng),
                            "y": str(user_lat),
                            "radius": str(radius_m),
                            "sort": "distance",
                            "page": str(page),
                            "size": "15",
                        },
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        logger.warning(f"카카오 병원 검색 실패 HTTP {resp.status_code}: {resp.text[:160]}")
                        break
                    data = resp.json()
                    docs = data.get("documents", [])
                    if not docs:
                        break
                    for d in docs:
                        name = _clean_hospital_name(d.get("place_name", ""))
                        category = d.get("category_name", "")
                        if not name or name in seen:
                            continue
                        if not _is_relevant_place(name, category, dept_name, inst_type):
                            continue
                        lat = _safe_float(d.get("y"))
                        lng = _safe_float(d.get("x"))
                        distance_km = round(float(d.get("distance") or 0) / 1000, 2) if d.get("distance") else 0.0
                        found.append({
                            "name": name,
                            "type": inst_type,
                            "dept": dept_name or _guess_dept_from_text(name + " " + category),
                            "address": d.get("road_address_name") or d.get("address_name") or "",
                            "hours": _open_status(inst_type),
                            "openStatus": _open_status(inst_type),
                            "fit": "현재 위치 기준 실제 장소 검색 결과",
                            "distanceKm": distance_km,
                            "telno": d.get("phone", ""),
                            "lat": lat,
                            "lng": lng,
                            "naverMapUrl": _naver_map_url(name, d.get("road_address_name") or d.get("address_name") or ""),
                            "placeUrl": d.get("place_url", ""),
                            "source": "kakao_local_distance",
                        })
                        seen.add(name)
                        if len(found) >= limit:
                            return found[:limit]
                    if data.get("meta", {}).get("is_end"):
                        break
    except Exception as e:
        logger.warning(f"카카오 주변 병원 검색 오류: {e}", exc_info=True)
        return []

    return found[:limit]


def _guess_dept_from_text(text: str) -> str:
    for dept in DEPT_CODE_MAP.keys():
        if dept in text:
            return dept
    for dept, aliases in {
        "정형외과": ["정형", "관절", "척추"],
        "이비인후과": ["이비인후"],
        "비뇨의학과": ["비뇨"],
        "피부과": ["피부"],
        "산부인과": ["산부인", "여성"],
    }.items():
        if any(a in text for a in aliases):
            return dept
    return DEFAULT_DEPT




# ═══════════════════════════════════════
#  네이버 지도/지역 검색 API — 현재 좌표 기준 실제 병원 후보 검색
# ═══════════════════════════════════════

def _strip_html(text: str) -> str:
    import re
    return re.sub(r'<[^>]+>', '', text or '').replace('&amp;', '&').strip()


def _naver_search_credentials() -> tuple[str, str]:
    settings = get_settings()
    cid = settings.NAVER_SEARCH_CLIENT_ID or settings.NAVER_CLIENT_ID
    secret = settings.NAVER_SEARCH_CLIENT_SECRET or settings.NAVER_CLIENT_SECRET
    return cid, secret


def _naver_maps_credentials() -> tuple[str, str]:
    settings = get_settings()
    cid = settings.NAVER_MAPS_CLIENT_ID or settings.NAVER_CLIENT_ID
    secret = settings.NAVER_MAPS_CLIENT_SECRET or settings.NAVER_CLIENT_SECRET
    return cid, secret


def _region_keyword_from_reverse_geocode(data: dict) -> str:
    try:
        results = data.get('results') or []
        if not results:
            return ''
        region = results[0].get('region', {})
        names = []
        for key in ['area1', 'area2', 'area3']:
            name = (region.get(key) or {}).get('name')
            if name and name not in names:
                names.append(name)
        return ' '.join(names[:2]) or ' '.join(names)
    except Exception:
        return ''


async def reverse_geocode_naver(user_lat: Optional[float], user_lng: Optional[float]) -> str:
    """네이버 지도 Reverse Geocoding으로 현재 좌표의 행정구역 키워드를 얻습니다."""
    cid, secret = _naver_maps_credentials()
    if not (cid and secret and user_lat and user_lng):
        return ''
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                'https://naveropenapi.apigw.ntruss.com/map-reversegeocode/v2/gc',
                params={
                    'coords': f'{user_lng},{user_lat}',
                    'orders': 'admcode,roadaddr,addr',
                    'output': 'json',
                },
                headers={
                    'X-NCP-APIGW-API-KEY-ID': cid,
                    'X-NCP-APIGW-API-KEY': secret,
                },
            )
        if resp.status_code == 200:
            return _region_keyword_from_reverse_geocode(resp.json())
        logger.warning(f'네이버 Reverse Geocoding 실패 HTTP {resp.status_code}: {resp.text[:160]}')
    except Exception as e:
        logger.warning(f'네이버 Reverse Geocoding 오류: {e}')
    return ''


async def geocode_address_naver(address: str) -> Optional[dict]:
    """네이버 지도 Geocoding으로 주소를 좌표로 변환합니다."""
    cid, secret = _naver_maps_credentials()
    if not (cid and secret and address):
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                'https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode',
                params={'query': address},
                headers={
                    'X-NCP-APIGW-API-KEY-ID': cid,
                    'X-NCP-APIGW-API-KEY': secret,
                },
            )
        if resp.status_code == 200:
            addresses = resp.json().get('addresses', [])
            if addresses:
                return {'lat': float(addresses[0]['y']), 'lng': float(addresses[0]['x'])}
        logger.debug(f'네이버 Geocoding 결과 없음/실패: {address}')
    except Exception as e:
        logger.warning(f'네이버 Geocoding 오류: {address} | {e}')
    return None


async def get_naver_driving_distance_km(
    user_lat: Optional[float], user_lng: Optional[float], hosp_lat: Optional[float], hosp_lng: Optional[float]
) -> Optional[float]:
    """네이버 Directions API가 설정되어 있으면 길찾기 거리(km)를 얻습니다."""
    cid, secret = _naver_maps_credentials()
    if not (cid and secret and user_lat and user_lng and hosp_lat and hosp_lng):
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                'https://naveropenapi.apigw.ntruss.com/map-direction/v1/driving',
                params={
                    'start': f'{user_lng},{user_lat}',
                    'goal': f'{hosp_lng},{hosp_lat}',
                    'option': 'trafast',
                },
                headers={
                    'X-NCP-APIGW-API-KEY-ID': cid,
                    'X-NCP-APIGW-API-KEY': secret,
                },
            )
        if resp.status_code == 200:
            route = resp.json().get('route', {})
            first = next(iter(route.values()), [])
            if first:
                dist_m = first[0].get('summary', {}).get('distance')
                if dist_m is not None:
                    return round(float(dist_m) / 1000, 2)
    except Exception as e:
        logger.debug(f'네이버 Directions 거리 계산 실패: {e}')
    return None


def _naver_query_terms(dept_name: str, inst_type: str, region_kw: str = '') -> list[str]:
    dept = normalize_dept_name(dept_name or DEFAULT_DEPT)
    stem = dept.replace('의학과', '').strip()
    prefix = (region_kw or '').strip()
    base = []
    if inst_type == '대학병원':
        base = [f'{prefix} 대학병원 {dept}', f'{prefix} 상급종합병원 {dept}', f'{prefix} 종합병원 {dept}']
    elif inst_type == '전문병원':
        base = [f'{prefix} {dept} 병원', f'{prefix} {stem} 병원', f'{prefix} 전문병원 {dept}']
    else:
        base = [f'{prefix} {dept} 의원', f'{prefix} {stem} 의원', f'{prefix} {dept} 클리닉']
    # 지역 키워드가 너무 좁아 결과가 없을 때를 대비한 보조 검색어
    base.extend([q.strip() for q in [f'{dept} {inst_type}', f'{dept} 의원'] if q.strip() not in base])
    return [q.strip() for q in base if q.strip()]


def _passes_inst_type_filter(name: str, category: str, inst_type: str) -> bool:
    text = f'{name} {category}'
    if inst_type == '의원':
        return ('의원' in text or '클리닉' in text) and not any(x in text for x in ['대학병원', '상급종합'])
    if inst_type == '전문병원':
        return ('병원' in text or '의료원' in text) and '의원' not in name and '대학병원' not in text
    if inst_type == '대학병원':
        return any(x in text for x in ['대학병원', '상급종합', '종합병원', '의료원', '병원']) and '의원' not in name
    return True


async def search_hospitals_naver_nearby(
    dept_name: str = '',
    inst_type: str = '의원',
    user_lat: Optional[float] = None,
    user_lng: Optional[float] = None,
    region: str = '',
    limit: int = 20,
) -> list[dict]:
    """
    네이버 지역검색 + 네이버 지도 Geocoding/Directions로 실제 병원 후보를 좌표화하고
    사용자 GPS 기준 가까운 순서로 정렬합니다.
    """
    search_id, search_secret = _naver_search_credentials()
    if not (search_id and search_secret):
        logger.warning('NAVER_SEARCH_CLIENT_ID/SECRET 미설정 — 네이버 지역검색 불가')
        return []

    region_kw = (region or '').strip()
    if not region_kw and user_lat and user_lng:
        region_kw = await reverse_geocode_naver(user_lat, user_lng)

    headers = {
        'X-Naver-Client-Id': search_id,
        'X-Naver-Client-Secret': search_secret,
    }
    found: list[dict] = []
    seen: set[str] = set()
    terms = _naver_query_terms(dept_name, inst_type, region_kw)

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for query in terms:
                resp = await client.get(
                    'https://openapi.naver.com/v1/search/local.json',
                    params={'query': query, 'display': '20', 'start': '1', 'sort': 'random'},
                    headers=headers,
                )
                if resp.status_code != 200:
                    logger.warning(f'네이버 지역검색 실패 HTTP {resp.status_code}: {resp.text[:160]}')
                    continue
                for item in resp.json().get('items', []):
                    raw_name = _strip_html(item.get('title', ''))
                    name = _clean_hospital_name(raw_name)
                    if not name or name in seen:
                        continue
                    category = _strip_html(item.get('category', ''))
                    if not _is_relevant_place(name, category, dept_name, inst_type):
                        continue
                    if not _passes_inst_type_filter(name, category, inst_type):
                        continue
                    address = item.get('roadAddress') or item.get('address') or ''
                    coords = await geocode_address_naver(address) if address else None
                    lat = coords['lat'] if coords else None
                    lng = coords['lng'] if coords else None
                    straight_km = None
                    if user_lat and user_lng and lat and lng:
                        straight_km = round(haversine_km(user_lat, user_lng, lat, lng), 2)
                    route_km = await get_naver_driving_distance_km(user_lat, user_lng, lat, lng) if straight_km is not None else None
                    sort_km = route_km if route_km is not None else (straight_km if straight_km is not None else 9999)
                    found.append({
                        'name': name,
                        'type': inst_type,
                        'dept': dept_name or _guess_dept_from_text(name + ' ' + category),
                        'address': address,
                        'hours': _open_status(inst_type),
                        'openStatus': _open_status(inst_type),
                        'fit': '네이버 지역검색 기반 실제 의료기관 후보',
                        'distanceKm': straight_km if straight_km is not None else 0.0,
                        'routeDistanceKm': route_km,
                        'sortDistanceKm': sort_km,
                        'telno': item.get('telephone', ''),
                        'lat': lat,
                        'lng': lng,
                        'naverMapUrl': _naver_map_url(name, address),
                        'source': 'naver_local_geocoded',
                    })
                    seen.add(name)
                    if len(found) >= max(limit * 2, limit):
                        break
                if len(found) >= max(limit * 2, limit):
                    break
    except Exception as e:
        logger.warning(f'네이버 주변 병원 검색 오류: {e}', exc_info=True)
        return []

    # 길찾기 거리 또는 직선거리 기준 정렬
    found.sort(key=lambda h: h.get('sortDistanceKm', 9999))
    return found[:limit]




async def search_hospitals_kakao_region(
    dept_name: str = "",
    inst_type: str = "의원",
    region: str = "",
    limit: int = 20,
) -> list[dict]:
    """
    카카오맵 검색창 방식의 지역명 + 진료과 실제 장소 검색.

    원칙:
    - 사용자가 카카오맵에 직접 입력하는 형태의 검색어를 그대로 만든다.
      예) "충북 청주시 상당구 안과", "대전 서구 탄방동 이비인후과"
    - 동네 의원: 지역 + 진료과 중심.
    - 전문 병원: 지역 + 진료과, 지역 + 진료과 + 병원/전문병원 중심.
      카카오 API는 전문의 유무를 구조화해서 주지 않으므로 상세 여부는 지도 보기에서 확인한다.
    - 대학 병원: 지역 + 진료과 + 대학병원/대학교병원 중심.
    - 카카오 API에서 내려온 실제 place_name/address/place_url을 화면에 그대로 표시한다.
    - 과도한 지역/진료과/기관유형 필터링으로 실제 결과가 사라지는 문제를 피한다.
    """
    settings = get_settings()
    region = (region or "").strip()
    dept = normalize_dept_name(dept_name or DEFAULT_DEPT).strip()
    inst_type = (inst_type or "의원").strip()

    if not settings.KAKAO_REST_API_KEY:
        logger.warning("KAKAO_REST_API_KEY 미설정: 카카오 병원 검색 불가")
        return []
    if not region:
        logger.warning("지역명 없음: 카카오 병원 검색 불가")
        return []

    headers = {"Authorization": f"KakaoAK {settings.KAKAO_REST_API_KEY}"}

    def compact(text: str) -> str:
        return " ".join((text or "").split()).strip()

    # 광역자치단체 약칭/정식명 양방향 보정
    abbrev_to_full = {
        "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
        "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
        "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
        "전북": "전라북도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도", "제주": "제주특별자치도",
    }
    full_to_abbrev = {v: k for k, v in abbrev_to_full.items()}

    raw = compact(region)
    variants = []
    def add_region(r: str):
        r = compact(r)
        if r and r not in variants:
            variants.append(r)

    add_region(raw)
    norm = raw
    for full, short in full_to_abbrev.items():
        norm = norm.replace(full, short)
    add_region(norm)
    for short, full in abbrev_to_full.items():
        if norm.startswith(short + " "):
            add_region(norm.replace(short, full, 1))
    parts = [x for x in norm.split() if x]
    # 상세 주소가 동까지 들어오면 시군구, 구동, 동 단위까지 순차 검색
    for n in [4, 3, 2, 1]:
        if len(parts) >= n:
            add_region(" ".join(parts[:n]))
    if len(parts) >= 3:
        add_region(" ".join(parts[1:3]))  # 예: 청주시 상당구
        add_region(" ".join(parts[-2:]))  # 예: 상당구 용암동
    if len(parts) >= 2:
        add_region(parts[-1])

    # 진료과 별칭. 카카오맵 검색어와 맞추기 위해 과명/의원/병원 조합을 모두 시도한다.
    dept_alias_map = {
        "비뇨의학과": ["비뇨의학과", "비뇨기과"],
        "이비인후과": ["이비인후과"],
        "정신건강의학과": ["정신건강의학과", "정신과"],
        "가정의학과": ["가정의학과"],
        "재활의학과": ["재활의학과"],
        "소화기내과": ["소화기내과", "내과"],
        "알레르기내과": ["알레르기내과", "내과"],
        "감염내과": ["감염내과", "내과"],
        "응급의학과": ["응급의학과", "응급실", "종합병원"],
        "유방외과": ["유방외과", "외과"],
        "영상의학과": ["영상의학과", "영상의학센터"],
        "류마티스내과": ["류마티스내과", "내과"],
        "산부인과": ["산부인과", "여성의원"],
        "치과": ["치과"],
        "안과": ["안과"],
        "정형외과": ["정형외과"],
        "신경과": ["신경과"],
        "신경외과": ["신경외과"],
        "피부과": ["피부과"],
        "내과": ["내과"],
        "외과": ["외과"],
        "한의원": ["한의원", "한방병원"],
    }
    dept_aliases = []
    for d in dept_alias_map.get(dept, [dept]):
        d = compact(d)
        if d and d not in dept_aliases:
            dept_aliases.append(d)
    stem = dept.replace("의학과", "").replace("과", "").strip()
    if stem and len(stem) >= 2 and stem not in dept_aliases:
        dept_aliases.append(stem)

    queries: list[str] = []
    def add_query(q: str):
        q = compact(q)
        if q and q not in queries:
            queries.append(q)

    primary_regions = variants[:8]
    is_univ = inst_type == "대학병원"
    is_special = inst_type == "전문병원"

    for r in primary_regions:
        for d in dept_aliases:
            if is_univ:
                # 카카오맵에서 대학병원 검색할 때 사용자가 입력하는 방식
                for q in [
                    f"{r} {d} 대학병원",
                    f"{r} {d} 대학교병원",
                    f"{r} 대학병원 {d}",
                    f"{r} 대학교병원 {d}",
                    f"{r} 대학병원",
                    f"{r} 대학교병원",
                    f"{r} 종합병원 {d}",
                ]:
                    add_query(q)
            elif is_special:
                # 전문의/전문병원 여부는 카카오 API 필드로 바로 판정하기 어렵기 때문에
                # 진료과 병원 검색 결과를 보여주고 상세는 지도 보기에서 확인하게 한다.
                for q in [
                    f"{r} {d}",
                    f"{r} {d} 병원",
                    f"{r} {d} 전문병원",
                    f"{r} {d}의원",
                    f"{r} {d} 의원",
                ]:
                    add_query(q)
            else:
                # 동네 의원: 카카오맵 검색창과 같은 가장 단순한 검색어를 우선한다.
                for q in [
                    f"{r} {d}",
                    f"{r} {d}의원",
                    f"{r} {d} 의원",
                    f"{r} {d} 병원",
                ]:
                    add_query(q)

    # 마지막 보조 검색. 진료과 결과가 적을 때만 지역 의료기관 후보를 확보한다.
    for r in primary_regions[:4]:
        if is_univ:
            for q in [f"{r} 대학병원", f"{r} 대학교병원", f"{r} 종합병원"]:
                add_query(q)
        elif is_special:
            for q in [f"{r} 병원", f"{r} 전문병원", f"{r} 종합병원"]:
                add_query(q)
        else:
            for q in [f"{r} 의원", f"{r} 병원", f"{r} 의료기관"]:
                add_query(q)

    found: list[dict] = []
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for query in queries:
                logger.info(f"Kakao map-style hospital query: {query}")
                for page in range(1, 4):
                    resp = await client.get(
                        "https://dapi.kakao.com/v2/local/search/keyword.json",
                        params={"query": query, "page": str(page), "size": "15", "sort": "accuracy"},
                        headers=headers,
                    )
                    logger.info(f"Kakao status={resp.status_code} query='{query}' page={page}")
                    if resp.status_code != 200:
                        logger.warning(f"카카오 장소 검색 실패 HTTP {resp.status_code}: {resp.text[:300]}")
                        break
                    data = resp.json()
                    docs = data.get("documents", []) or []
                    logger.info(f"Kakao documents={len(docs)} query='{query}' page={page}")
                    if not docs:
                        break
                    for item in docs:
                        name = _clean_hospital_name(item.get("place_name", ""))
                        address = item.get("road_address_name") or item.get("address_name") or ""
                        category = item.get("category_name", "") or ""
                        if not name or not address:
                            continue
                        # API 검색어 자체가 지역+진료과/의료기관이므로, 실제 장소 결과는 최대한 표시한다.
                        # 단, 명백한 광고/비의료 키워드만 제외한다. Kakao Local API에는 웹 지도 광고 카드가 포함되지 않는다.
                        if any(bad in name for bad in ["광고", "AD", "유니클로", "렌터카", "호텔", "카페"]):
                            continue
                        key = f"{name}|{address}"
                        if key in seen:
                            continue
                        guessed_dept = dept_name or _guess_dept_from_text(name + " " + category) or dept
                        place_url = item.get("place_url", "")
                        if "대학" in name or "대학교" in name or "종합병원" in name or "의료원" in name:
                            out_type = "대학병원" if is_univ else ("전문병원" if is_special else "병원")
                        elif "병원" in name:
                            out_type = "전문병원" if is_special else "병원"
                        else:
                            out_type = "의원"
                        found.append({
                            "name": name,
                            "type": out_type,
                            "dept": guessed_dept,
                            "address": address,
                            "hours": "지도에서 진료시간 확인",
                            "openStatus": "지도에서 진료시간 확인",
                            "fit": f"{query} 카카오 장소 검색 결과",
                            "distanceKm": 0.0,
                            "telno": item.get("phone", ""),
                            "lat": _safe_float(item.get("y")),
                            "lng": _safe_float(item.get("x")),
                            "naverMapUrl": place_url or _naver_map_url(name, address),
                            "placeUrl": place_url,
                            "source": "kakao_local_keyword",
                            "category": category,
                            "query": query,
                            "specialistNote": "전문의 여부는 지도 상세에서 확인" if is_special else "",
                        })
                        seen.add(key)
                        if len(found) >= limit:
                            return found[:limit]
                    if data.get("meta", {}).get("is_end"):
                        break
    except Exception as e:
        logger.warning(f"카카오 지역 병원 검색 오류: {e}", exc_info=True)
        return []

    return found[:limit]



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
            if xmltodict is None:
                logger.error("xmltodict 미설치 — XML 응답 파싱 불가")
                return []
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
                "hours": _open_status(_simplify_type(type_name)),
                "openStatus": _open_status(_simplify_type(type_name)),
                "fit": f"의사 {item.get('drTotCnt', '?')}명",
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

# ═══════════════════════════════════════
#  Frontend v5 호환: 실제 병원명 후보 보정
# ═══════════════════════════════════════

DEPT_ALIAS_MAP = {
    "외과(대장항문)": "대장항문외과",
    "항문외과": "대장항문외과",
    "구강악안면외과": "치과",
    "알레르기내과": "내과",
    "소화기내과": "내과",
    "호흡기내과": "내과",
    "심장내과": "내과",
    "류마티스내과": "내과",
    "유방외과": "외과",
}

# 실제 존재하는 의료기관명을 기반으로 한 fallback 후보입니다.
# 공공데이터 API 키가 있을 경우에는 HIRA 검색 결과를 우선 사용하고,
# 부족한 경우에만 아래 후보로 화면 공백을 보완합니다.
REAL_HOSPITAL_CANDIDATES = {
    "신경과": {
        "의원": [
            ("서울신경과의원", "서울특별시 강남구"),
            ("연세신경과의원", "서울특별시 서초구"),
            ("강남연세신경과의원", "서울특별시 강남구"),
            ("삼성신경과의원", "서울특별시 강남구"),
            ("서울척탑병원", "서울특별시 강남구"),
        ],
        "전문병원": [
            ("서울척병원", "서울특별시 성북구"),
            ("우리들병원", "서울특별시 강남구"),
            ("나누리병원", "서울특별시 강남구"),
            ("서울부민병원", "서울특별시 강서구"),
            ("바른세상병원", "경기도 성남시"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("강남세브란스병원", "서울특별시 강남구"),
        ],
    },
    "피부과": {
        "의원": [
            ("차앤박피부과의원", "서울특별시 강남구"),
            ("오라클피부과의원", "서울특별시 강남구"),
            ("고운세상피부과의원", "서울특별시 강남구"),
            ("연세스타피부과의원", "서울특별시 강남구"),
            ("이지함피부과의원", "서울특별시 강남구"),
        ],
        "전문병원": [
            ("강남차병원", "서울특별시 강남구"),
            ("서울의료원", "서울특별시 중랑구"),
            ("한림대학교강남성심병원", "서울특별시 영등포구"),
            ("을지대학교병원", "대전광역시 서구"),
            ("대전성모병원", "대전광역시 중구"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("충남대학교병원", "대전광역시 중구"),
        ],
    },
    "내과": {
        "의원": [
            ("강남하나로내과의원", "서울특별시 강남구"),
            ("서울내과의원", "서울특별시 강남구"),
            ("연세내과의원", "서울특별시 서초구"),
            ("삼성내과의원", "서울특별시 강남구"),
            ("속편한내과의원", "서울특별시 강남구"),
        ],
        "전문병원": [
            ("하나이비인후과병원", "서울특별시 강남구"),
            ("서울성심병원", "서울특별시 동대문구"),
            ("강남베드로병원", "서울특별시 강남구"),
            ("대전선병원", "대전광역시 중구"),
            ("대전성모병원", "대전광역시 중구"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("충남대학교병원", "대전광역시 중구"),
        ],
    },
    "정형외과": {
        "의원": [
            ("강남연세정형외과의원", "서울특별시 강남구"),
            ("서울정형외과의원", "서울특별시 강남구"),
            ("바른세상정형외과의원", "서울특별시 강남구"),
            ("삼성본정형외과의원", "서울특별시 강남구"),
            ("연세본정형외과의원", "서울특별시 서초구"),
        ],
        "전문병원": [
            ("서울척병원", "서울특별시 성북구"),
            ("우리들병원", "서울특별시 강남구"),
            ("나누리병원", "서울특별시 강남구"),
            ("바른세상병원", "경기도 성남시"),
            ("대전우리병원", "대전광역시 서구"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("충남대학교병원", "대전광역시 중구"),
        ],
    },
    "이비인후과": {
        "의원": [
            ("하나이비인후과의원", "서울특별시 강남구"),
            ("연세이비인후과의원", "서울특별시 강남구"),
            ("서울이비인후과의원", "서울특별시 강남구"),
            ("코모키이비인후과의원", "서울특별시 강남구"),
            ("삼성이비인후과의원", "서울특별시 강남구"),
        ],
        "전문병원": [
            ("하나이비인후과병원", "서울특별시 강남구"),
            ("보아스이비인후과병원", "서울특별시 강남구"),
            ("다인이비인후과병원", "인천광역시 부평구"),
            ("대전선병원", "대전광역시 중구"),
            ("을지대학교병원", "대전광역시 서구"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("충남대학교병원", "대전광역시 중구"),
        ],
    },
    "비뇨의학과": {
        "의원": [
            ("타워비뇨의학과의원", "서울특별시 강남구"),
            ("골드만비뇨의학과의원", "서울특별시 강남구"),
            ("맨스톤비뇨의학과의원", "서울특별시 강남구"),
            ("서울비뇨기과의원", "서울특별시 강남구"),
            ("연세비뇨의학과의원", "서울특별시 서초구"),
        ],
        "전문병원": [
            ("강남차병원", "서울특별시 강남구"),
            ("서울의료원", "서울특별시 중랑구"),
            ("대전선병원", "대전광역시 중구"),
            ("대전성모병원", "대전광역시 중구"),
            ("을지대학교병원", "대전광역시 서구"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("충남대학교병원", "대전광역시 중구"),
        ],
    },
    "산부인과": {
        "의원": [
            ("차움의원", "서울특별시 강남구"),
            ("미즈메디병원", "서울특별시 강서구"),
            ("강남차병원", "서울특별시 강남구"),
            ("호산여성병원", "서울특별시 강남구"),
            ("청담마리산부인과의원", "서울특별시 강남구"),
        ],
        "전문병원": [
            ("강남차병원", "서울특별시 강남구"),
            ("미즈메디병원", "서울특별시 강서구"),
            ("제일병원", "서울특별시 중구"),
            ("서울여성병원", "대전광역시 서구"),
            ("대전미즈여성병원", "대전광역시 서구"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("충남대학교병원", "대전광역시 중구"),
        ],
    },
    "외과": {
        "의원": [
            ("서울외과의원", "서울특별시 강남구"),
            ("연세외과의원", "서울특별시 강남구"),
            ("강남외과의원", "서울특별시 강남구"),
            ("서울유방외과의원", "서울특별시 강남구"),
            ("유방외과의원", "서울특별시 강남구"),
        ],
        "전문병원": [
            ("민병원", "서울특별시 강북구"),
            ("대항병원", "서울특별시 서초구"),
            ("강남차병원", "서울특별시 강남구"),
            ("대전선병원", "대전광역시 중구"),
            ("대전성모병원", "대전광역시 중구"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("충남대학교병원", "대전광역시 중구"),
        ],
    },
    "가정의학과": {
        "의원": [
            ("서울가정의학과의원", "서울특별시 강남구"),
            ("연세가정의학과의원", "서울특별시 강남구"),
            ("삼성가정의학과의원", "서울특별시 강남구"),
            ("강남하나로의원", "서울특별시 강남구"),
            ("서울베스트의원", "서울특별시 강남구"),
        ],
        "전문병원": [
            ("서울의료원", "서울특별시 중랑구"),
            ("강남베드로병원", "서울특별시 강남구"),
            ("대전선병원", "대전광역시 중구"),
            ("대전성모병원", "대전광역시 중구"),
            ("을지대학교병원", "대전광역시 서구"),
        ],
        "대학병원": [
            ("서울대학교병원", "서울특별시 종로구"),
            ("세브란스병원", "서울특별시 서대문구"),
            ("서울아산병원", "서울특별시 송파구"),
            ("삼성서울병원", "서울특별시 강남구"),
            ("충남대학교병원", "대전광역시 중구"),
        ],
    },
}

DEFAULT_DEPT = "가정의학과"


def normalize_dept_name(dept_name: str) -> str:
    dept = (dept_name or DEFAULT_DEPT).strip()
    return DEPT_ALIAS_MAP.get(dept, dept)


def _open_status(inst_type: str = "의원") -> str:
    """현재 시간 기준 진료 상태 참고값. 실시간 지도/병원 접수 상태는 아님."""
    try:
        now = datetime.now(ZoneInfo("Asia/Seoul")) if ZoneInfo else datetime.now()
    except Exception:
        now = datetime.now()
    day = now.weekday()  # 월=0, 일=6
    minutes = now.hour * 60 + now.minute
    if inst_type == "대학병원":
        start, end = 8 * 60 + 30, 17 * 60
    elif inst_type == "전문병원":
        start, end = 9 * 60, 17 * 60 + 30
    else:
        start, end = 9 * 60, 18 * 60
    is_open = (0 <= day <= 4 and start <= minutes < end) or (inst_type == "의원" and day == 5 and 9 * 60 <= minutes < 13 * 60)
    return "진료중" if is_open else "진료종료"


def _naver_map_url(name: str, address: str = "") -> str:
    from urllib.parse import quote
    q = quote(f"{name} {address}".strip())
    return f"https://map.naver.com/p/search/{q}"



# 주요 fallback 의료기관의 대략 좌표입니다.
# DATA_GO_KR_API_KEY가 있으면 HIRA가 반환한 XPos/YPos를 우선 사용하고,
# API 결과가 없을 때만 이 좌표로 현재 GPS와의 직선거리 참고값을 계산합니다.
APPROX_HOSPITAL_COORDS = {
    "강남세브란스병원": (37.4929, 127.0463), "삼성서울병원": (37.4883, 127.0852), "서울아산병원": (37.5266, 127.1084),
    "서울대학교병원": (37.5798, 126.9990), "세브란스병원": (37.5624, 126.9408), "가톨릭대학교 서울성모병원": (37.5017, 127.0048),
    "서울성모병원": (37.5017, 127.0048), "강남차병원": (37.5068, 127.0340), "차움의원": (37.5242, 127.0442),
    "하나이비인후과병원": (37.4965, 127.0296), "누네안과병원": (37.5047, 127.0489), "강남베드로병원": (37.4886, 127.0327),
    "나누리병원": (37.4920, 127.0307), "연세사랑병원": (37.4877, 127.0175), "대항병원": (37.4850, 127.0124),
    "민병원": (37.6386, 127.0252), "미즈메디병원": (37.5580, 126.8468), "국립재활원": (37.6389, 127.0118),
    "국립정신건강센터": (37.5651, 127.0855), "충남대학교병원": (36.3167, 127.4150), "대전선병원": (36.3225, 127.4202),
    "대전성모병원": (36.3210, 127.4208), "을지대학교병원": (36.3548, 127.3816), "대전우리병원": (36.3481, 127.3779),
}

REGION_CENTER_COORDS = {
    "강남구": (37.5172, 127.0473), "서초구": (37.4837, 127.0324), "송파구": (37.5145, 127.1059),
    "종로구": (37.5735, 126.9788), "서대문구": (37.5791, 126.9368), "강서구": (37.5509, 126.8495),
    "중랑구": (37.6063, 127.0927), "성북구": (37.5894, 127.0167), "강북구": (37.6396, 127.0257),
    "대전": (36.3504, 127.3845), "중구": (36.3250, 127.4213), "서구": (36.3555, 127.3839),
}


def _clean_hospital_name(name: str) -> str:
    import re
    cleaned = (name or '').strip()
    cleaned = re.sub(r'\s*(강남점|역삼점|선릉점|압구정점|청담점|논현점|서초점|송파점|잠실점|도곡점|대치점|삼성점)$', '', cleaned)
    return cleaned.strip()


def _approx_coords_for_hospital(name: str, address: str = '') -> Optional[tuple[float, float]]:
    clean = _clean_hospital_name(name)
    if clean in APPROX_HOSPITAL_COORDS:
        return APPROX_HOSPITAL_COORDS[clean]
    for key, coords in APPROX_HOSPITAL_COORDS.items():
        if key in clean or clean in key:
            return coords
    text = f"{address or ''} {name or ''}"
    for key, coords in REGION_CENTER_COORDS.items():
        if key in text:
            return coords
    return None


def _distance_or_fallback(user_lat: Optional[float], user_lng: Optional[float], name: str, address: str, fallback_km: float) -> tuple[float, Optional[float], Optional[float]]:
    coords = _approx_coords_for_hospital(name, address)
    if coords and user_lat and user_lng:
        return round(haversine_km(user_lat, user_lng, coords[0], coords[1]), 1), coords[0], coords[1]
    if coords:
        return round(fallback_km, 1), coords[0], coords[1]
    return round(fallback_km, 1), None, None

def build_fallback_hospitals(
    dept_name: str,
    inst_type: str = "의원",
    region: str = "",
    limit: int = 5,
    user_lat: Optional[float] = None,
    user_lng: Optional[float] = None,
) -> list[dict]:
    """프론트가 비지 않도록 실제 병원명 후보를 생성합니다."""
    dept = normalize_dept_name(dept_name)
    data = REAL_HOSPITAL_CANDIDATES.get(dept) or REAL_HOSPITAL_CANDIDATES.get(DEFAULT_DEPT)
    rows = data.get(inst_type) or data.get("의원") or []
    base_km = {"의원": 0.6, "전문병원": 1.6, "대학병원": 3.0}.get(inst_type, 1.0)
    hours = {"의원": "평일 주간 (9:00~18:00)", "전문병원": "평일 (9:00~17:00)", "대학병원": "평일 (8:30~17:00)"}.get(inst_type, "평일 주간")
    fit_suffix = {"의원": "1차 진료", "전문병원": "전문 진료", "대학병원": "정밀검사·협진"}.get(inst_type, "진료")
    result = []
    for i, (name, address) in enumerate(rows[:limit]):
        fallback_km = base_km + i * 0.7
        distance_km, lat, lng = _distance_or_fallback(user_lat, user_lng, name, address or region, fallback_km)
        result.append({
            "name": name,
            "type": inst_type,
            "dept": dept_name or dept,
            "address": address or region or "현재 위치 주변",
            "hours": hours,
            "openStatus": _open_status(inst_type),
            "fit": f"{dept_name or dept} 관련 {fit_suffix}",
            "distanceKm": distance_km,
            "lat": lat,
            "lng": lng,
            "naverMapUrl": _naver_map_url(name, address or region),
            "source": "fallback_real_name_candidate_gps_sorted" if user_lat and user_lng else "fallback_real_name_candidate",
        })
    result.sort(key=lambda h: h.get("distanceKm", 999))
    return result


async def build_frontend_hospitals(
    region: str = "",
    departments: list[str] | None = None,
    user_lat: Optional[float] = None,
    user_lng: Optional[float] = None,
    limit_per_type: int = 5,
) -> list[dict]:
    """frontend v5가 기대하는 nearbyHospitals 구조를 진료과×병원급 전체로 구성."""
    deps = [d for d in (departments or []) if d]
    if not deps:
        deps = [DEFAULT_DEPT]

    inst_types = ["의원", "전문병원", "대학병원"]
    all_items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for dept in deps[:3]:
        norm = normalize_dept_name(dept)
        for inst_type in inst_types:
            found: list[dict] = []
            # HIRA API는 의원/병원/상급종합 식의 종별 코드가 더 정확합니다.
            hira_inst_type = {"의원": "의원", "전문병원": "병원", "대학병원": "상급종합"}.get(inst_type, inst_type)
            try:
                found = await search_hospitals_hira(
                    region=region,
                    dept_name=norm,
                    inst_type=hira_inst_type,
                    user_lat=user_lat,
                    user_lng=user_lng,
                    radius_m=7000,
                    num_of_rows=limit_per_type,
                )
            except Exception as e:
                logger.warning(f"HIRA 병원 검색 실패: dept={dept} type={inst_type} error={e}")
                found = []

            cleaned = []
            for h in found:
                # 프론트 필터와 맞게 type을 사용자가 누르는 버튼값으로 통일
                if not h.get("name"):
                    continue
                cleaned.append({
                    "name": h.get("name", ""),
                    "type": inst_type,
                    "dept": dept,
                    "address": h.get("address", region or ""),
                    "hours": h.get("hours") or ("평일 주간 (9:00~18:00)" if inst_type == "의원" else "평일 진료"),
                    "openStatus": h.get("openStatus") or _open_status(inst_type),
                    "fit": h.get("fit") or f"{dept} 관련 진료",
                    "distanceKm": h.get("distanceKm") or 0.0,
                    "telno": h.get("telno", ""),
                    "naverMapUrl": _naver_map_url(h.get("name", ""), h.get("address", "")),
                    "source": "hira",
                })

            if len(cleaned) < limit_per_type:
                cleaned.extend(build_fallback_hospitals(dept, inst_type, region, limit_per_type - len(cleaned), user_lat=user_lat, user_lng=user_lng))

            for h in cleaned[:limit_per_type]:
                key = (h.get("name", ""), h.get("type", ""), h.get("dept", ""))
                if key in seen:
                    continue
                seen.add(key)
                all_items.append(h)

    return all_items
