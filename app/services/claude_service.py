"""
Claude API 프록시 서비스
프론트엔드의 callClaude() 함수를 서버로 이전
- API 키 서버 보관
- 프롬프트 버전 관리
- 응답 검증 및 재시도
- 요청/응답 로깅
"""

import json
import logging
import httpx
from app.core.config import get_settings
from app.models.schemas import AnalyzeRequest, AnalyzeResponse, DiseaseInfo

logger = logging.getLogger("mediroute.claude")

# ═══════════════════════════════════════
#  프롬프트 템플릿 (버전 관리)
# ═══════════════════════════════════════

PROMPT_VERSION = "v1.0"

SYSTEM_PROMPT = """당신은 보건의료 정보 안내 전문가입니다.
주의: 이 서비스는 진단이 아닌 의료기관 선택 안내 서비스입니다.
진단적 표현 없이 안내 중심으로 작성하세요.
반드시 JSON 오브젝트만 반환하세요. 마크다운, 설명, 코드블록 없이 순수 JSON만."""


def build_user_prompt(req: AnalyzeRequest) -> str:
    """사용자 입력으로부터 Claude에 보낼 프롬프트 생성"""
    symptom_desc = req.symptom or (
        ", ".join(req.areas) + " 부위 증상" if req.areas else "증상 입력 없음"
    )
    region = req.region or "대전"

    return f"""환자 정보:
- 증상: {symptom_desc}
- 부위: {", ".join(req.areas) if req.areas else "미선택"}
- 나이: {req.age or "미입력"}세 {req.gender or ""}
- 지역: {region}
- 증상 시작: {req.duration or "미입력"}
- 응급 체크: {", ".join(req.checks) if req.checks else "없음"}
- 복용약: {req.meds or "없음"}
- 기저질환: {req.disease or "없음"}

반환 JSON 형식:
{{
  "isUrgent": false,
  "areaText": "증상 부위 요약 (15자 이내)",
  "symptomText": "주요 증상 요약 (15자 이내)",
  "urgencyText": "낮음(일반 진료 권장) or 보통(빠른 진료 권장) or 높음(즉시 진료 또는 응급 평가 권장). 흉통·호흡곤란·의식변화·마비 → 높음, 고열·급성통증·혈변 → 보통, 그 외 → 낮음",
  "predictedDiseases": [
    {{"name":"예상 질환명","reason":"증상 근거 (35자 이내)"}},
    {{"name":"예상 질환명","reason":"증상 근거 (35자 이내)"}},
    {{"name":"예상 질환명","reason":"증상 근거 (35자 이내)"}}
  ],
  "dept1": {{"name":"진료과","reason":"이유 (30자 이내)"}},
  "dept2": {{"name":"진료과","reason":"이유 (30자 이내)"}},
  "dept3": {{"name":"진료과","reason":"이유 (30자 이내)"}},
  "hospGuide": "병원급 선택 안내 한 줄 (40자 이내)",
  "memoSay": ["말할 내용1","말할 내용2","말할 내용3","말할 내용4","말할 내용5"],
  "memoAsk": ["질문1","질문2","질문3","질문4"],
  "routeBest": "가장 좋은 치료 경로 설명 (50자 이내)",
  "routeFast": "가장 빠른 진료 경로 설명 (50자 이내)",
  "routeCheap": "가장 저비용 경로 설명 (50자 이내)",
  "routePro": "전문 진료 경로 설명 (50자 이내)",
  "costClinic": "의원 본인부담 금액 범위",
  "costTest": "검사 비용 범위",
  "costTreat": "치료 비용 범위",
  "costInpat": "입원 비용 범위",
  "nearbyHospitals": [
    {{"name":"병원명","type":"의원 or 전문병원 or 대학병원","dept":"진료과","address":"주소","hours":"진료시간","fit":"적합도 설명","distanceKm":0.8}},
    {{"name":"병원명","type":"의원 or 전문병원 or 대학병원","dept":"진료과","address":"주소","hours":"진료시간","fit":"적합도 설명","distanceKm":1.5}},
    {{"name":"병원명","type":"의원 or 전문병원 or 대학병원","dept":"진료과","address":"주소","hours":"진료시간","fit":"적합도 설명","distanceKm":3.0}}
  ]
}}

지역이 "{region}"이므로 nearbyHospitals는 해당 지역 실제 병원명과 주소로 작성하세요."""


# ═══════════════════════════════════════
#  Claude API 호출
# ═══════════════════════════════════════

async def call_claude(req: AnalyzeRequest, max_retries: int = 2) -> dict:
    """
    Claude API를 호출하고 파싱된 JSON을 반환
    - 응답이 유효한 JSON이 아니면 재시도
    - max_retries 횟수만큼 재시도 후 실패 시 예외 발생
    """
    settings = get_settings()

    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    user_prompt = build_user_prompt(req)

    for attempt in range(max_retries + 1):
        try:
            logger.info(
                f"Claude API 호출 (시도 {attempt + 1}/{max_retries + 1}) | "
                f"prompt_version={PROMPT_VERSION} | "
                f"symptom={req.symptom[:30]}... | region={req.region}"
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                    },
                    json={
                        "model": settings.CLAUDE_MODEL,
                        "max_tokens": settings.CLAUDE_MAX_TOKENS,
                        "system": SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_prompt}],
                    },
                )

            if resp.status_code != 200:
                error_body = resp.text
                logger.error(f"Claude API HTTP {resp.status_code}: {error_body[:200]}")
                if attempt < max_retries:
                    continue
                raise RuntimeError(f"Claude API 오류 (HTTP {resp.status_code})")

            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")

            # JSON 파싱 (코드블록 제거)
            clean = text.replace("```json", "").replace("```", "").strip()
            result = json.loads(clean)

            logger.info(
                f"Claude API 성공 | "
                f"diseases={[d.get('name','?') for d in result.get('predictedDiseases',[])]} | "
                f"urgent={result.get('isUrgent', False)}"
            )

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 파싱 실패 (시도 {attempt + 1}): {e}")
            if attempt < max_retries:
                continue
            raise ValueError(f"Claude 응답을 JSON으로 파싱할 수 없습니다: {e}")

        except httpx.TimeoutException:
            logger.warning(f"Claude API 타임아웃 (시도 {attempt + 1})")
            if attempt < max_retries:
                continue
            raise RuntimeError("Claude API 응답 시간 초과 (30초)")

    raise RuntimeError("Claude API 호출 실패 (최대 재시도 초과)")


# ═══════════════════════════════════════
#  분석 실행 (메인 진입점)
# ═══════════════════════════════════════

async def analyze_symptoms(
    req: AnalyzeRequest,
    user_lat: float | None = None,
    user_lng: float | None = None,
) -> AnalyzeResponse:
    """
    증상 분석 메인 함수
    1. Claude API 호출 (질환 예측 + 진료과 추천)
    2. 심평원 API로 실제 병원 데이터 조회 (STEP 2)
    3. Claude의 가짜 병원 목록을 실제 데이터로 교체
    """
    from app.services.hospital_service import search_hospitals_hira, geocode_address
    from app.core.config import get_settings

    raw = await call_claude(req)

    # 프론트 응급 체크 + Claude 판단 합산
    if req.is_urgent:
        raw["isUrgent"] = True
        if raw.get("urgencyText") == "낮음":
            raw["urgencyText"] = "높음"

    # ── STEP 2: 프론트 병원 리스트에 맞춘 주변 의료기관 데이터 생성 ──
    # 프론트는 nearbyHospitals를 진료과/병원급(type)으로 필터링한다.
    # 따라서 dept1뿐 아니라 dept1~3 전체에 대해 의원·전문병원·대학병원 데이터가 필요하다.
    try:
        from app.services.hospital_service import (
            search_hospitals_hira,
            geocode_address,
            merge_and_fill_hospitals,
            normalize_dept_name,
        )
        from app.core.config import get_settings

        dept_names: list[str] = []
        for key in ("dept1", "dept2", "dept3"):
            if raw.get(key) and isinstance(raw[key], dict) and raw[key].get("name"):
                dept_names.append(normalize_dept_name(raw[key]["name"]))
        if not dept_names:
            dept_names = ["가정의학과"]
        dept_names = list(dict.fromkeys(dept_names))

        search_lat, search_lng = user_lat, user_lng
        if not (search_lat and search_lng) and req.region:
            coords = await geocode_address(req.region)
            if coords:
                search_lat = coords["lat"]
                search_lng = coords["lng"]

        settings = get_settings()
        real_hospitals: list[dict] = []
        if settings.DATA_GO_KR_API_KEY:
            for dept_name in dept_names:
                try:
                    found = await search_hospitals_hira(
                        region=req.region or "대전",
                        dept_name=dept_name,
                        user_lat=search_lat,
                        user_lng=search_lng,
                        radius_m=5000,
                        num_of_rows=20,
                    )
                    real_hospitals.extend(found)
                except Exception as e:
                    logger.warning(f"{dept_name} 병원 조회 실패: {e}")

        raw["nearbyHospitals"] = merge_and_fill_hospitals(
            real_rows=real_hospitals,
            region=req.region or "현재 위치 주변",
            dept_names=dept_names,
            per_type=5,
        )
        logger.info(
            f"프론트용 병원 데이터 생성 완료: {len(raw['nearbyHospitals'])}건 "
            f"| depts={dept_names} | real={len(real_hospitals)}"
        )
    except Exception as e:
        logger.warning(f"병원 데이터 보강 실패, Claude 결과 유지: {e}")

    # ── STEP 3: KCD 기반 비용 데이터 보강 ──
    try:
        from app.services.kcd_service import symptom_to_kcd, get_cost_by_kcd

        # Claude가 예측한 질환명으로 KCD 매핑 시도
        symptom_text = req.symptom or " ".join(req.areas)
        kcd_matches = symptom_to_kcd(symptom_text, areas=req.areas, top_n=1)

        if kcd_matches:
            best_kcd = kcd_matches[0]["kcd"]
            cost_data = get_cost_by_kcd(best_kcd)

            if cost_data:
                # Claude의 추정 비용 → 실제 통계 데이터로 교체
                if cost_data.get("cost_clinic"):
                    raw["costClinic"] = cost_data["cost_clinic"]
                if cost_data.get("cost_test"):
                    raw["costTest"] = cost_data["cost_test"]
                if cost_data.get("cost_treat"):
                    raw["costTreat"] = cost_data["cost_treat"]
                if cost_data.get("cost_inpat"):
                    raw["costInpat"] = cost_data["cost_inpat"]

                logger.info(
                    f"KCD 비용 데이터 적용: {best_kcd} ({cost_data['name']})"
                )

        # 진료 필요도 판정 (부위 + 증상 기반)
        from app.services.kcd_service import assess_urgency
        backend_urgency = assess_urgency(symptom_text, req.areas, req.is_urgent, req.duration)
        if not raw.get("urgencyText") or raw["urgencyText"] == "낮음":
            raw["urgencyText"] = backend_urgency
    except Exception as e:
        logger.warning(f"KCD 비용 보강 실패: {e}")

    # Pydantic 모델로 검증
    try:
        response = AnalyzeResponse(**raw)
    except Exception as e:
        logger.warning(f"응답 스키마 검증 실패, 부분 적용: {e}")
        response = AnalyzeResponse(
            isUrgent=raw.get("isUrgent", req.is_urgent),
            areaText=raw.get("areaText", ""),
            symptomText=raw.get("symptomText", ""),
            urgencyText=raw.get("urgencyText", "낮음"),
            predictedDiseases=[
                DiseaseInfo(**d) for d in raw.get("predictedDiseases", [])
                if isinstance(d, dict) and "name" in d
            ],
            hospGuide=raw.get("hospGuide", ""),
        )

    return response
