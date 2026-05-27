"""
진료비 조회 서비스 (STEP 3)
- 심평원 질병정보서비스 API 연동 (질환별 진료 통계)
- 비급여진료비정보조회서비스 API 연동
- KCD 내장 데이터 폴백
"""

import logging
import xmltodict
import httpx
from typing import Optional
from app.core.config import get_settings
from app.services.kcd_service import get_cost_by_kcd, KCD_MASTER

logger = logging.getLogger("mediroute.cost")


# ═══════════════════════════════════════
#  심평원 질병정보서비스 API
#  질환별 성별/연령별/지역별/종별 통계
# ═══════════════════════════════════════

HIRA_DISEASE_BASE = "http://apis.data.go.kr/B551182/diseaseInfoService/getDissInfoList"


async def fetch_disease_stats(
    kcd_code: str,
    year: str = "2024",
) -> Optional[dict]:
    """
    심평원 질병정보서비스에서 KCD 코드별 통계 조회

    반환: {
        "kcd": "M17",
        "patient_count": 1234567,
        "visit_count": 5678901,
        "total_expense": 1234567890,
        "avg_expense_per_visit": 217,
    }
    """
    settings = get_settings()
    if not settings.DATA_GO_KR_API_KEY:
        return None

    params = {
        "serviceKey": settings.DATA_GO_KR_API_KEY,
        "numOfRows": "10",
        "pageNo": "1",
        "_type": "json",
        "sickType": "1",  # 주상병 기준
        "medTp": "1",     # 건강보험
        "diseaseCode": kcd_code,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(HIRA_DISEASE_BASE, params=params)

        if resp.status_code != 200:
            logger.warning(f"질병정보서비스 HTTP {resp.status_code}")
            return None

        content_type = resp.headers.get("content-type", "")
        if "json" in content_type or resp.text.strip().startswith("{"):
            data = resp.json()
        else:
            data = xmltodict.parse(resp.text)

        body = data.get("response", {}).get("body", {})
        items = body.get("items", {})
        if not items:
            return None

        item_list = items.get("item", [])
        if isinstance(item_list, dict):
            item_list = [item_list]

        if not item_list:
            return None

        # 첫 번째 항목의 통계 추출
        item = item_list[0]
        patient_count = _safe_int(item.get("patientCount", item.get("insupPatCnt", 0)))
        visit_count = _safe_int(item.get("visitCount", item.get("rcptCnt", 0)))
        total_expense = _safe_int(item.get("medExpense", item.get("totExpense", 0)))

        avg_per_visit = round(total_expense / visit_count) if visit_count > 0 else 0

        return {
            "kcd": kcd_code,
            "patient_count": patient_count,
            "visit_count": visit_count,
            "total_expense": total_expense,
            "avg_expense_per_visit": avg_per_visit,
        }

    except Exception as e:
        logger.warning(f"질병정보서비스 조회 실패: {e}")
        return None


# ═══════════════════════════════════════
#  비급여진료비정보조회서비스 API
# ═══════════════════════════════════════

HIRA_NONCOVERED_BASE = "http://apis.data.go.kr/B551182/nonPaymentDamtInfoService"


async def fetch_noncovered_costs(
    item_code: str = "",
    item_name: str = "",
) -> list[dict]:
    """
    비급여 항목별 비용 조회 (종별/지역별 통계)

    예: MRI, 초음파, 도수치료 등의 실제 비급여 가격
    """
    settings = get_settings()
    if not settings.DATA_GO_KR_API_KEY:
        return []

    # 종별 평균 비용 조회
    endpoint = f"{HIRA_NONCOVERED_BASE}/getNonPaymentDamtClList"
    params = {
        "serviceKey": settings.DATA_GO_KR_API_KEY,
        "numOfRows": "20",
        "pageNo": "1",
        "_type": "json",
    }
    if item_code:
        params["npayKorNm"] = item_code
    if item_name:
        params["npayKorNm"] = item_name

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(endpoint, params=params)

        if resp.status_code != 200:
            return []

        content_type = resp.headers.get("content-type", "")
        if "json" in content_type or resp.text.strip().startswith("{"):
            data = resp.json()
        else:
            data = xmltodict.parse(resp.text)

        body = data.get("response", {}).get("body", {})
        items = body.get("items", {})
        if not items:
            return []

        item_list = items.get("item", [])
        if isinstance(item_list, dict):
            item_list = [item_list]

        results = []
        for item in item_list:
            results.append({
                "name": item.get("npayKorNm", ""),
                "inst_type": item.get("clCdNm", ""),
                "min_cost": _safe_int(item.get("minAmt", 0)),
                "max_cost": _safe_int(item.get("maxAmt", 0)),
                "avg_cost": _safe_int(item.get("avgAmt", item.get("mdnAmt", 0))),
            })

        return results

    except Exception as e:
        logger.warning(f"비급여 조회 실패: {e}")
        return []


# ═══════════════════════════════════════
#  통합 비용 조회 (메인 진입점)
# ═══════════════════════════════════════

async def get_comprehensive_cost(
    kcd_code: str,
    region: str = "",
    inst_type: str = "",
) -> dict:
    """
    KCD 코드 기반 종합 비용 정보 반환

    1순위: 심평원 API 실시간 조회
    2순위: KCD 마스터 테이블 (내장 데이터)
    """
    result = {
        "kcd": kcd_code,
        "name": "",
        "category": "",
        "dept": "",
        "cost_clinic": "",
        "cost_test": "",
        "cost_treat": "",
        "cost_inpat": "",
        "api_stats": None,      # 심평원 API 통계
        "noncovered": [],       # 비급여 항목
        "source": "내장 데이터",  # 데이터 출처
    }

    # 1. 내장 KCD 데이터
    kcd_info = get_cost_by_kcd(kcd_code)
    if kcd_info:
        result.update(kcd_info)

    # 2. 심평원 질병정보서비스 API
    api_stats = await fetch_disease_stats(kcd_code)
    if api_stats:
        result["api_stats"] = api_stats
        result["source"] = "심평원 API + 내장 데이터"

        # 건당 진료비로 비용 보강
        avg = api_stats.get("avg_expense_per_visit", 0)
        if avg > 0:
            result["cost_clinic"] = (
                f"건당 평균 {avg:,}원 (건강보험 기준)"
                if not result["cost_clinic"]
                else result["cost_clinic"]
            )

    # 3. 관련 비급여 항목 조회 (MRI, 초음파 등)
    noncovered_keywords = _get_noncovered_keywords(kcd_code)
    for keyword in noncovered_keywords:
        items = await fetch_noncovered_costs(item_name=keyword)
        if items:
            result["noncovered"].extend(items[:3])

    if result["noncovered"]:
        result["source"] += " + 비급여 실시간 조회"

    return result


def _get_noncovered_keywords(kcd_code: str) -> list[str]:
    """KCD 코드에 따라 관련 비급여 항목 키워드 반환"""
    kcd3 = kcd_code[:3] if len(kcd_code) >= 3 else kcd_code

    # 근골격계 → MRI, 도수치료, 체외충격파
    if kcd3 in ("M17", "M51", "M75", "M50", "M48", "S93"):
        return ["MRI", "도수치료"]
    # 소화기계 → 내시경 수면
    if kcd3 in ("C16", "C18", "K25", "K57", "K21"):
        return ["수면내시경"]
    # 피부 → 레이저
    if kcd3 in ("L30",):
        return ["피부레이저"]

    return []


def _safe_int(val) -> int:
    try:
        return int(float(val)) if val else 0
    except (ValueError, TypeError):
        return 0
