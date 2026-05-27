"""
Claude API 프록시 + rule-based fallback 서비스
- frontend v5(mediroute_frontend_real_hospitals_first_button.html) 응답 구조 호환
- ANTHROPIC_API_KEY가 없어도 /api/analyze가 실패하지 않도록 서버 내부 분석 결과 반환
- 추천 진료과·예상 질환에 맞춰 nearbyHospitals를 병원급별 실제 병원명 후보로 보강
"""

import json
import logging
import httpx
from app.core.config import get_settings
from app.models.schemas import AnalyzeRequest, AnalyzeResponse, DiseaseInfo, DeptInfo

logger = logging.getLogger("mediroute.claude")

PROMPT_VERSION = "v2.0-real-hospital-compatible"

SYSTEM_PROMPT = """당신은 보건의료 정보 안내 전문가입니다.
주의: 이 서비스는 진단이 아닌 의료기관 선택 안내 서비스입니다.
진단적 표현 없이 안내 중심으로 작성하세요.
반드시 JSON 오브젝트만 반환하세요. 마크다운, 설명, 코드블록 없이 순수 JSON만."""


def build_user_prompt(req: AnalyzeRequest) -> str:
    symptom_desc = req.symptom or (
        ", ".join(req.areas) + " 부위 증상" if req.areas else "증상 입력 없음"
    )
    region = req.region or "현재 위치 주변"

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
  "areaText": "증상 부위 요약",
  "symptomText": "주요 증상 요약",
  "urgencyText": "낮음 or 보통 or 높음",
  "predictedDiseases": [
    {{"name":"예상 질환명","reason":"증상 근거"}},
    {{"name":"예상 질환명","reason":"증상 근거"}},
    {{"name":"예상 질환명","reason":"증상 근거"}}
  ],
  "dept1": {{"name":"진료과","reason":"이유"}},
  "dept2": {{"name":"진료과","reason":"이유"}},
  "dept3": {{"name":"진료과","reason":"이유"}},
  "hospGuide": "병원급 선택 안내",
  "memoSay": ["말할 내용1","말할 내용2","말할 내용3","말할 내용4","말할 내용5"],
  "memoAsk": ["질문1","질문2","질문3","질문4"],
  "routeBest": "가장 좋은 치료 경로 설명",
  "routeFast": "가장 빠른 진료 경로 설명",
  "routeCheap": "가장 저비용 경로 설명",
  "routePro": "전문 진료 경로 설명",
  "costClinic": "의원 본인부담 금액 범위",
  "costTest": "검사 비용 범위",
  "costTreat": "치료 비용 범위",
  "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
  "nearbyHospitals": []
}}

nearbyHospitals는 서버에서 HIRA/실제 병원명 후보로 다시 보강하므로 빈 배열로 두어도 됩니다."""


async def call_claude(req: AnalyzeRequest, max_retries: int = 2) -> dict:
    settings = get_settings()
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다. rule-based fallback을 사용합니다.")

    user_prompt = build_user_prompt(req)

    for attempt in range(max_retries + 1):
        try:
            logger.info(
                f"Claude API 호출 (시도 {attempt + 1}/{max_retries + 1}) | "
                f"prompt_version={PROMPT_VERSION} | symptom={req.symptom[:30]}... | region={req.region}"
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
                logger.error(f"Claude API HTTP {resp.status_code}: {resp.text[:200]}")
                if attempt < max_retries:
                    continue
                raise RuntimeError(f"Claude API 오류 (HTTP {resp.status_code})")

            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")
            clean = text.replace("```json", "").replace("```", "").strip()
            return json.loads(clean)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 파싱 실패 (시도 {attempt + 1}): {e}")
            if attempt < max_retries:
                continue
            raise
        except httpx.TimeoutException:
            logger.warning(f"Claude API 타임아웃 (시도 {attempt + 1})")
            if attempt < max_retries:
                continue
            raise RuntimeError("Claude API 응답 시간 초과")

    raise RuntimeError("Claude API 호출 실패")


def _contains_any(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)



def _is_male(req: AnalyzeRequest) -> bool:
    return ("남" in str(req.gender or "")) or (str(req.gender or "").lower() == "male")


def _is_female(req: AnalyzeRequest) -> bool:
    return ("여" in str(req.gender or "")) or (str(req.gender or "").lower() == "female")


def _profile_text(req: AnalyzeRequest) -> str:
    return f"{req.symptom or ''} {' '.join(req.areas or [])} {req.disease or ''}"




def _strip_possibility_from_diseases(raw: dict) -> dict:
    """질환명에서 '가능성' 표현을 제거해 화면에 확정적 후보명처럼 간결하게 표시합니다."""
    if not raw:
        return raw
    cleaned = []
    for item in raw.get("predictedDiseases") or []:
        if isinstance(item, dict):
            new_item = dict(item)
            name = str(new_item.get("name", "")).replace(" 가능성", "").replace("가능성", "").replace(" 의심", "").strip()
            new_item["name"] = name
            cleaned.append(new_item)
        elif item:
            cleaned.append({"name": str(item).replace(" 가능성", "").replace("가능성", "").replace(" 의심", "").strip(), "reason": "입력 증상 기반 확인"})
    if cleaned:
        raw["predictedDiseases"] = cleaned
    return raw

def _sanitize_gender_departments(req: AnalyzeRequest, raw: dict) -> dict:
    """성별·부위와 맞지 않는 진료과를 보정합니다.

    예: 50세 남성 + 생식기·비뇨기 증상에서 산부인과가 나오지 않도록
    비뇨의학과/피부과/내과 중심으로 교체합니다.
    """
    if not raw:
        return raw

    male = _is_male(req)
    female = _is_female(req)
    context = _profile_text(req)

    depts = []
    for key in ("dept1", "dept2", "dept3"):
        d = raw.get(key)
        if isinstance(d, dict) and d.get("name"):
            depts.append({"name": d.get("name", ""), "reason": d.get("reason", "증상에 따른 진료 가능")})

    if male:
        if any(x in context for x in ["생식기", "비뇨기", "골반", "사타구니"]):
            depts = [
                {"name": "비뇨의학과", "reason": "남성 비뇨기·생식기 증상 확인 가능"},
                {"name": "피부과", "reason": "피부 병변·가려움·분비물 동반 시 확인 가능"},
                {"name": "내과", "reason": "감염·전신 증상 동반 여부 확인 가능"},
            ]
            raw["predictedDiseases"] = [
                {"name": "요로감염", "reason": "배뇨통·빈뇨·분비물 여부 확인"},
                {"name": "전립선/비뇨기 질환 확인", "reason": "남성 생식기·골반 증상 확인"},
                {"name": "피부염 또는 감염", "reason": "가려움·분비물·발진 동반 시 확인"},
            ]
        elif "유방" in context:
            depts = [
                {"name": "유방외과", "reason": "유방 멍울·통증·분비물 확인 가능"},
                {"name": "외과", "reason": "흉부 표면 병변·염증 확인 가능"},
                {"name": "내분비내과", "reason": "호르몬·전신 원인 감별 가능"},
            ]
        else:
            depts = [d for d in depts if "산부인과" not in str(d.get("name", ""))]

    if (not male) and female and any(x in context for x in ["생식기", "골반", "사타구니"]) and any(x in context for x in ["질", "분비물", "골반", "월경", "생리", "임신", "여성"]):
        depts = [
            {"name": "산부인과", "reason": "여성 골반·생식기 증상 확인 가능"},
            {"name": "비뇨의학과", "reason": "배뇨통·요로감염 확인 가능"},
            {"name": "감염내과", "reason": "감염성 원인 확인 가능"},
        ]

    # 귀 + 분비물/가려움은 피부 증상처럼 보이더라도 이비인후과가 우선입니다.
    if "귀" in context and any(x in context for x in ["분비물", "진물", "가려움", "먹먹", "청력", "이명", "통증"]):
        depts = [
            {"name": "이비인후과", "reason": "귀 분비물·외이도·고막 상태 확인 가능"},
            {"name": "피부과", "reason": "귀 주변 피부염·가려움 확인 가능"},
            {"name": "알레르기내과", "reason": "반복 가려움·알레르기 반응 확인 가능"},
        ]
        raw["predictedDiseases"] = [
            {"name": "외이도염", "reason": "귀 분비물·가려움 확인"},
            {"name": "중이염", "reason": "귀 분비물·먹먹함 동반 여부 확인"},
            {"name": "귀 주변 피부염", "reason": "귀 주변 피부 자극·가려움 확인"},
        ]

    # 중복 제거 및 빈 자리 보완
    seen = set()
    cleaned = []
    for d in depts:
        name = d.get("name")
        if name and name not in seen:
            cleaned.append(d)
            seen.add(name)

    fallback = [
        {"name": "가정의학과", "reason": "초진 후 필요한 진료과 의뢰 가능"},
        {"name": "내과", "reason": "전신 증상·감염 확인 가능"},
        {"name": "외과", "reason": "통증·염증·상처 확인 가능"},
    ]
    if male:
        fallback = [
            {"name": "비뇨의학과", "reason": "비뇨기 증상 확인 가능"},
            {"name": "내과", "reason": "감염·전신 증상 확인 가능"},
            {"name": "피부과", "reason": "피부 병변 동반 시 확인 가능"},
        ]

    for d in fallback:
        if len(cleaned) >= 3:
            break
        if d["name"] not in seen:
            cleaned.append(d)
            seen.add(d["name"])

    # 최종 안전장치: 남성 환자에게 산부인과가 노출되지 않게 제거합니다.
    if male:
        cleaned = [d for d in cleaned if "산부인과" not in str(d.get("name", ""))]
        if any(x in context for x in ["생식기", "비뇨기", "골반", "사타구니"]):
            raw["predictedDiseases"] = [
                {"name": "요로감염", "reason": "배뇨통·빈뇨·분비물 여부 확인"},
                {"name": "전립선/비뇨기 질환 확인", "reason": "남성 생식기·골반 증상 확인"},
                {"name": "피부염 또는 감염", "reason": "가려움·분비물·발진 동반 시 확인"},
            ]

    for i, d in enumerate(cleaned[:3], start=1):
        raw[f"dept{i}"] = d
    return raw


def _base_common(req: AnalyzeRequest, urgency: str) -> dict:
    area_text = ", ".join(req.areas) if req.areas else "미선택"
    symptom_text = req.symptom[:35] if req.symptom else area_text
    return {
        "isUrgent": req.is_urgent or urgency == "높음",
        "areaText": area_text,
        "symptomText": symptom_text,
        "urgencyText": urgency,
        "hospGuide": "먼저 가까운 의원에서 초진 후 필요 시 전문 병원 또는 대학 병원으로 의뢰받는 경로를 권장합니다.",
        "memoSay": [
            "증상이 시작된 시점과 악화되는 상황",
            "통증·가려움·분비물의 위치와 강도",
            "발열, 구토, 마비, 시야 변화 등 동반 증상",
            "복용 중인 약과 기저질환",
            "사진이 있다면 증상 변화 시점",
        ],
        "memoAsk": [
            "현재 증상에서 우선 의심되는 원인은 무엇인가요?",
            "검사나 영상 촬영이 필요한가요?",
            "약물 치료와 생활 관리 중 무엇을 먼저 해야 하나요?",
            "전문 병원이나 대학 병원 의뢰가 필요한 시점은 언제인가요?",
        ],
        "routeBest": "가까운 1차 의원에서 초진 후 필요한 검사와 전문 진료를 단계적으로 진행합니다.",
        "routeFast": "증상이 심하거나 급격히 악화되면 전문 병원 또는 응급 진료를 우선 고려합니다.",
        "routeCheap": "1차 의원에서 기본 진료 후 꼭 필요한 검사만 진행해 비용 부담을 줄입니다.",
        "routePro": "반복·만성·고위험 증상은 해당 전문과 또는 대학 병원 협진을 고려합니다.",
        "costClinic": "5,000~20,000원",
        "costTest": "10,000~80,000원",
        "costTreat": "5,000~100,000원",
        "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        "nearbyHospitals": [],
    }


def build_rule_based_analysis(req: AnalyzeRequest) -> dict:
    text = f"{req.symptom} {' '.join(req.areas)}".lower()
    areas = req.areas or []
    urgency = "높음" if req.is_urgent or _contains_any(text, ["호흡곤란", "의식", "마비", "흉통", "시력저하", "최악", "갑자기"]) else "낮음"
    if urgency != "높음" and _contains_any(text, ["고열", "혈변", "심한", "반복", "구토", "어지러움", "분비물", "붓기"]):
        urgency = "보통"

    # 부위·증상별 질환/진료과 매칭
    if "머리·두피" in areas:
        if _contains_any(text, ["가려움", "분비물", "발진", "각질", "비듬", "뾰루지", "종기"]):
            diseases = [
                {"name": "두피 피부염", "reason": "두피 가려움·분비물 확인 필요"},
                {"name": "지루성 피부염", "reason": "각질·비듬·가려움 동반 가능"},
                {"name": "두피 감염", "reason": "분비물·종기·열감 확인 필요"},
            ]
            depts = [
                {"name": "피부과", "reason": "두피 피부 병변 확인 가능"},
                {"name": "가정의학과", "reason": "초진 후 전문과 의뢰 가능"},
                {"name": "이비인후과", "reason": "인접 부위 염증 확인 가능"},
            ]
        else:
            diseases = [
                {"name": "긴장성 두통", "reason": "스트레스·자세 관련 두통"},
                {"name": "편두통", "reason": "반복적 박동성 두통 확인"},
                {"name": "두피 질환", "reason": "두피 가려움·발진 동반 확인"},
            ]
            depts = [
                {"name": "신경과", "reason": "두통 원인 감별·진료 가능"},
                {"name": "내과", "reason": "전신 원인 두통 확인 가능"},
                {"name": "가정의학과", "reason": "초진 후 의뢰 가능"},
            ]
    elif "유방" in areas:
        breast_skin_symptom = _contains_any(text, ["가려움", "간지러움", "분비물", "진물", "유두분비", "습진", "발진", "홍반"])
        if breast_skin_symptom:
            diseases = [
                {"name": "유방 피부질환", "reason": "유방 가려움·피부 변화 확인"},
                {"name": "유관질환 감별", "reason": "유두 분비물 동반 여부 확인"},
                {"name": "유방질환 감별", "reason": "가려움·분비물 지속 시 진료 필요"},
            ]
            if _is_male(req):
                depts = [
                    {"name": "피부과", "reason": "유방 피부 병변 확인 가능"},
                    {"name": "유방외과", "reason": "남성 유방 분비물·염증 확인 가능"},
                    {"name": "가정의학과", "reason": "초진 후 필요한 진료과 의뢰 가능"},
                ]
            else:
                depts = [
                    {"name": "피부과", "reason": "유방 피부 가려움·발진 확인 가능"},
                    {"name": "산부인과", "reason": "여성 유방·호르몬 관련 상담 가능"},
                    {"name": "가정의학과", "reason": "초진 후 필요한 진료과 의뢰 가능"},
                ]
        else:
            diseases = [
                {"name": "유방 통증·염증", "reason": "유방 통증·부종 확인 필요"},
                {"name": "유방 양성 병변 확인", "reason": "멍울·압통 여부 진료 필요"},
                {"name": "유선 관련 질환 확인", "reason": "분비물·피부 변화 확인"},
            ]
            if _is_male(req):
                depts = [
                    {"name": "유방외과", "reason": "유방 증상 전문 확인 가능"},
                    {"name": "외과", "reason": "흉부 표면 병변·염증 확인 가능"},
                    {"name": "내분비내과", "reason": "호르몬·전신 원인 감별 가능"},
                ]
            else:
                depts = [
                    {"name": "유방외과", "reason": "유방 증상 전문 확인 가능"},
                    {"name": "산부인과", "reason": "여성 유방·호르몬 상담 가능"},
                    {"name": "가정의학과", "reason": "초진 후 의뢰 가능"},
                ]
    elif "피부" in areas or _contains_any(text, ["가려움", "발진", "두드러기", "홍반", "각질"]):
        diseases = [
            {"name": "접촉성 피부염", "reason": "발진·가려움 증상 관련"},
            {"name": "두드러기", "reason": "가려움·붓기 확인 필요"},
            {"name": "피부 감염", "reason": "분비물·열감·부종 확인"},
        ]
        depts = [
            {"name": "피부과", "reason": "피부 병변 전문 확인 가능"},
            {"name": "알레르기내과", "reason": "알레르기 반응 확인 가능"},
            {"name": "가정의학과", "reason": "초진 후 의뢰 가능"},
        ]
    elif any(a in areas for a in ["입·치아", "턱·침샘", "목·인후"]):
        diseases = [
            {"name": "침샘염", "reason": "턱 밑 붓기·분비물 확인 필요"},
            {"name": "타석증", "reason": "식사 시 통증·부종 반복 확인"},
            {"name": "구강·인후 감염", "reason": "입안·인후 염증 확인"},
        ]
        depts = [
            {"name": "이비인후과", "reason": "침샘·턱 밑 확인 가능"},
            {"name": "치과", "reason": "치아·구강 원인 확인 가능"},
            {"name": "내과", "reason": "감염·전신 증상 확인 가능"},
        ]
    elif any(a in areas for a in ["생식기·비뇨기", "골반·사타구니"]):
        if _is_male(req):
            diseases = [
                {"name": "요로감염", "reason": "배뇨통·빈뇨·분비물 여부 확인"},
                {"name": "전립선/비뇨기 질환 확인", "reason": "남성 생식기·골반 증상 확인"},
                {"name": "피부염 또는 감염", "reason": "가려움·분비물·발진 동반 시 확인"},
            ]
            depts = [
                {"name": "비뇨의학과", "reason": "남성 비뇨기·생식기 증상 확인 가능"},
                {"name": "피부과", "reason": "피부 병변·분비물 동반 확인 가능"},
                {"name": "내과", "reason": "감염·전신 증상 확인 가능"},
            ]
        else:
            diseases = [
                {"name": "요로감염", "reason": "빈뇨·배뇨통·혈뇨 확인"},
                {"name": "방광염", "reason": "하복부 통증·빈뇨 확인"},
                {"name": "골반·생식기 질환 확인", "reason": "성별·증상별 확인 필요"},
            ]
            depts = [
                {"name": "산부인과", "reason": "여성 골반·생식기 확인 가능"},
                {"name": "비뇨의학과", "reason": "비뇨기 전문 확인 가능"},
                {"name": "내과", "reason": "감염·전신 증상 확인 가능"},
            ]
    elif "손목·손가락" in areas:
        symptom_only = (req.symptom or "").lower()
        explicit_wrist = "손목" in symptom_only
        explicit_finger = any(w in symptom_only for w in ["손가락", "손끝", "손마디"])
        neuro_sign = _contains_any(symptom_only, ["저림", "마비", "감각저하", "감각 이상", "찌릿", "힘빠", "힘 빠", "근력저하"])
        if explicit_wrist and neuro_sign:
            diseases = [
                {"name": "손목터널증후군", "reason": "손목 저림·마비·감각저하 확인"},
                {"name": "경추 디스크 의심", "reason": "목에서 시작되는 저림 확인"},
                {"name": "말초신경병증 확인", "reason": "감각 이상 원인 확인 필요"},
            ]
            depts = [
                {"name": "정형외과", "reason": "손목 관절·신경 확인 가능"},
                {"name": "신경과", "reason": "신경 압박 원인 확인 가능"},
                {"name": "재활의학과", "reason": "근전도 검사·평가 가능"},
            ]
        elif explicit_finger:
            diseases = [
                {"name": "손가락 건초염", "reason": "손가락 통증·부종 확인 필요"},
                {"name": "손가락 관절염", "reason": "손가락 관절 통증·붓기 확인"},
                {"name": "손가락 염좌 또는 인대 손상", "reason": "외상·반복 사용 후 손가락 통증 확인"},
            ]
            depts = [
                {"name": "정형외과", "reason": "손가락 관절·힘줄 확인 가능"},
                {"name": "재활의학과", "reason": "손가락 기능·통증 평가 가능"},
                {"name": "류마티스내과", "reason": "반복 관절염 여부 확인 가능"},
            ]
        elif neuro_sign:
            diseases = [
                {"name": "말초신경병증 확인", "reason": "손 저림·감각 이상 확인 필요"},
                {"name": "경추 디스크 의심", "reason": "목에서 시작되는 저림 확인"},
                {"name": "압박신경병증", "reason": "반복 사용·자세 관련 신경 압박 확인"},
            ]
            depts = [
                {"name": "신경과", "reason": "신경 증상 원인 확인 가능"},
                {"name": "정형외과", "reason": "손·팔 관절/신경 확인 가능"},
                {"name": "재활의학과", "reason": "근전도 검사·기능 평가 가능"},
            ]
        else:
            diseases = [
                {"name": "건초염", "reason": "손목 또는 손가락 통증·부종 확인 필요"},
                {"name": "관절염", "reason": "손가락 관절 통증·부종 확인"},
                {"name": "염좌 또는 인대 손상", "reason": "외상·반복 사용 후 통증 여부 확인"},
            ]
            depts = [
                {"name": "정형외과", "reason": "손목·손가락 확인 가능"},
                {"name": "재활의학과", "reason": "통증·기능 평가 가능"},
                {"name": "류마티스내과", "reason": "관절염 여부 확인 가능"},
            ]
    elif any(a in areas for a in ["어깨", "등·허리", "무릎", "팔·손", "다리·발", "발목·발가락"]):
        diseases = [
            {"name": "근골격계 염좌", "reason": "통증·운동 제한 확인"},
            {"name": "관절염 또는 힘줄 손상", "reason": "관절 통증·부종 확인"},
            {"name": "신경 압박 증상 확인", "reason": "저림·마비 동반 시 확인"},
        ]
        depts = [
            {"name": "정형외과", "reason": "관절·뼈·힘줄 확인 가능"},
            {"name": "재활의학과", "reason": "통증·기능 평가 가능"},
            {"name": "신경외과", "reason": "신경 압박 확인 가능"},
        ]
    elif "눈" in areas:
        diseases = [
            {"name": "결막염", "reason": "충혈·분비물 확인 필요"},
            {"name": "안구건조증", "reason": "이물감·통증 확인"},
            {"name": "시야 이상 확인", "reason": "시야 변화 동반 시 확인"},
        ]
        depts = [
            {"name": "안과", "reason": "눈 증상 전문 확인 가능"},
            {"name": "가정의학과", "reason": "초진 후 안과 의뢰 가능"},
            {"name": "응급의학과", "reason": "급격한 시력저하 시 확인"},
        ]
    else:
        diseases = [
            {"name": "만성 피로 증후군", "reason": "피로·무기력 지속 여부 확인"},
            {"name": "감염성 질환", "reason": "발열·통증·분비물 확인 필요"},
            {"name": "내분비 및 대사 질환", "reason": "갑상선·혈당·대사 이상 확인"},
        ]
        depts = [
            {"name": "가정의학과", "reason": "초진 종합 평가 가능"},
            {"name": "내과", "reason": "전신 증상·감염 확인 가능"},
            {"name": "외과", "reason": "염증·상처·통증 확인 가능"},
        ]

    raw = _base_common(req, urgency)
    raw.update({
        "predictedDiseases": diseases,
        "dept1": depts[0],
        "dept2": depts[1],
        "dept3": depts[2],
    })
    return _sanitize_gender_departments(req, raw)



# ═══════════════════════════════════════
#  부위·증상 기반 검사/비용 매칭
#  - 프론트 예상 비용 페이지와 동기화
#  - 부위보다 위험 신호/증상 키워드를 우선 적용
# ═══════════════════════════════════════

def _has_area(req: AnalyzeRequest, area_names: list[str]) -> bool:
    return any(a in (req.areas or []) for a in area_names)


def build_exam_cost_profile(req: AnalyzeRequest, raw: dict | None = None) -> dict:
    """
    선택 부위 + 증상 텍스트 + 예상 질환 + 추천 진료과를 함께 보고
    예상 검사·치료·비용 문구를 반환합니다.

    핵심 원칙:
    1) 중추신경 위험 신호가 있을 때만 뇌 CT/MRI를 우선합니다.
    2) 손목·손가락/팔·손/다리·발의 저림은 말초신경·근골격 검사로 보냅니다.
    3) 가려움·분비물·발진·홍반·진물은 부위가 팔다리여도 피부과 검사로 우선 매칭합니다.
    4) 외상·접질림·부종·운동 제한은 X-ray/초음파/MRI로 매칭합니다.
    """
    raw = raw or {}
    disease_text = " ".join(
        [d.get("name", "") + " " + d.get("reason", "") for d in raw.get("predictedDiseases", []) if isinstance(d, dict)]
    )
    dept_text = " ".join(
        [raw.get(k, {}).get("name", "") + " " + raw.get(k, {}).get("reason", "") for k in ["dept1", "dept2", "dept3"] if isinstance(raw.get(k), dict)]
    )
    text = f"{req.symptom or ''} {' '.join(req.areas or [])} {disease_text} {dept_text}".lower()

    def has(words: list[str]) -> bool:
        return _contains_any(text, words)

    upper_limb = _has_area(req, ["팔·손", "손목·손가락"])
    lower_limb = _has_area(req, ["다리·발", "발목·발가락", "무릎"])
    limb = upper_limb or lower_limb or _has_area(req, ["어깨"])
    trunk_msk = _has_area(req, ["등·허리", "어깨"])

    skin_sign = has(["가려움", "발진", "홍반", "분비물", "진물", "고름", "물집", "두드러기", "각질", "습진", "피부염", "무좀", "진균", "농가진"])
    trauma_sign = has(["접질", "삐", "외상", "넘어", "부딪", "타박", "골절", "염좌", "부종", "붓", "운동 제한", "움직", "통증"])
    numbness_sign = has(["저림", "마비", "감각저하", "찌릿", "전기", "힘빠", "근력", "손저림", "발저림"])
    central_neuro_redflag = has(["의식", "말 어눌", "발음", "한쪽 마비", "편측", "경련", "실신", "극심한 두통", "벼락두통", "시야장애", "시력저하", "갑자기 심한 두통"])

    profile = {
        "costClinic": "5,000~15,000원",
        "costTest": "20,000~80,000원\n기본 혈액검사·소변검사 등",
        "costTreat": "10,000~80,000원\n약물치료·기본 처치",
        "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        "routeBest": "가까운 1차 의원에서 초진 후 필요한 검사와 전문 진료를 단계적으로 진행합니다.",
        "routeFast": "증상이 심하거나 급격히 악화되면 전문 병원 또는 응급 진료를 우선 고려합니다.",
        "routeCheap": "1차 의원에서 기본 진료 후 꼭 필요한 검사만 진행해 비용 부담을 줄입니다.",
        "routePro": "반복·만성·고위험 증상은 해당 전문과 또는 대학 병원 협진을 고려합니다.",
    }

    # 0. 응급/중추신경 위험 신호: 이때만 뇌 CT/MRI 우선
    if central_neuro_redflag:
        profile.update({
            "costTest": "100,000~500,000원\n뇌 CT·MRI·혈액검사·신경학적 평가",
            "costTreat": "50,000~300,000원\n응급 처치·약물치료·전문 진료",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
            "routeFast": "의식저하·말 어눌함·편측 마비·경련이 있으면 즉시 응급실로 이동합니다.",
        })
        return profile

    # 0-1. 단순 두통/어지러움: 위험 신호가 없으면 CT/MRI 비용을 기본값으로 잡지 않음
    if _has_area(req, ["머리·두피"]) or has(["두통", "편두통", "어지러움", "긴장성 두통"]):
        profile.update({
            "costClinic": "5,000~20,000원",
            "costTest": "5,000~80,000원\n혈압 확인·신경학적 문진·필요 시 혈액검사",
            "costTreat": "5,000~60,000원\n진통제·생활요법·필요 시 예방약",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
            "routeBest": "가까운 의원 또는 신경과에서 혈압·신경학적 문진을 먼저 확인하고, 위험 신호가 있을 때만 CT/MRI를 고려합니다.",
            "routeFast": "갑작스러운 극심한 두통, 말 어눌함, 의식저하, 경련, 한쪽 마비가 있으면 응급실을 우선 고려합니다.",
        })
        return profile

    # 1. 피부 증상은 부위보다 우선. 손/발이어도 피부 검사로 매칭.
    if skin_sign:
        if _has_area(req, ["머리·두피"]):
            profile.update({
                "costTest": "10,000~70,000원\n피부확대경·진균검사·세균배양검사·필요 시 피부조직검사",
                "costTreat": "10,000~80,000원\n외용약·항진균제/항생제·두피 염증 치료",
                "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
                "routeBest": "피부과에서 두피 병변을 확인하고 필요한 경우 진균·세균 검사를 진행합니다.",
            })
        elif limb:
            extra = "·신경전도검사·근전도" if numbness_sign else ""
            profile.update({
                "costTest": f"10,000~120,000원\n피부확대경·진균검사·세균배양검사·염증검사{extra}",
                "costTreat": "10,000~100,000원\n외용약·소독·상처 드레싱·항생제/항진균제 처방",
                "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
                "routeBest": "피부과에서 발진·홍반·분비물 원인을 먼저 확인하고, 저림이 지속되면 신경 검사를 추가합니다.",
            })
        else:
            profile.update({
                "costTest": "10,000~100,000원\n피부확대경·알레르기검사·진균검사·세균배양검사·필요 시 조직검사",
                "costTreat": "10,000~100,000원\n외용약·항히스타민제·항생제/항진균제·피부 처치",
                "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
                "routeBest": "피부과에서 병변 형태를 확인한 뒤 필요한 검사만 단계적으로 진행합니다.",
            })
        return profile

    # 2. 손목·손가락/팔·손 저림: 뇌가 아니라 말초신경·근골격 검사
    if upper_limb and numbness_sign:
        symptom_only = (req.symptom or "").lower()
        if "손가락" in symptom_only and "손목" not in symptom_only:
            exam_label = "손가락 X-ray·초음파·필요 시 신경전도검사"
            route_best = "손가락 증상은 손가락 관절/힘줄 문제와 말초신경 이상을 구분해 확인합니다."
        elif "손목" in symptom_only:
            exam_label = "신경전도검사·근전도·손목 X-ray·초음파"
            route_best = "손목 저림·마비·감각저하는 손목터널증후군과 말초신경 압박을 우선 확인합니다."
        else:
            exam_label = "신경전도검사·근전도·손/손목 X-ray·초음파"
            route_best = "손 저림은 말초신경 압박과 손/손목 관절 문제를 우선 확인합니다."
        profile.update({
            "costTest": f"30,000~180,000원\n{exam_label}",
            "costTreat": "20,000~150,000원\n약물치료·보조기·물리치료·주사치료",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
            "routeBest": route_best,
        })
        return profile

    # 3. 다리·발 저림: 말초신경/허리 기원 감별
    if lower_limb and numbness_sign:
        profile.update({
            "costTest": "30,000~220,000원\n신경전도검사·근전도·요추/하지 X-ray·필요 시 MRI",
            "costTreat": "20,000~150,000원\n약물치료·물리치료·신경차단술 검토",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
            "routeBest": "정형외과·신경과에서 말초신경 이상과 허리 신경 압박 가능성을 함께 확인합니다.",
        })
        return profile

    # 4. 외상/통증/부종 중심 근골격계
    if limb and trauma_sign:
        area_name = "손/손목" if upper_limb else "발/발목/무릎"
        profile.update({
            "costTest": f"20,000~180,000원\n{area_name} X-ray·초음파·필요 시 MRI",
            "costTreat": "30,000~150,000원\n부목·보조기·물리치료·주사치료",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
            "routeBest": "정형외과에서 골절·염좌·힘줄 손상 여부를 먼저 확인합니다.",
        })
        return profile

    if trunk_msk and trauma_sign:
        profile.update({
            "costTest": "20,000~250,000원\n척추/관절 X-ray·초음파·필요 시 MRI",
            "costTreat": "30,000~180,000원\n약물치료·물리치료·도수치료·주사치료",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    # 5. 소화기
    if _has_area(req, ["배·소화기", "옆구리"]) or has(["복통", "소화", "설사", "구토", "구역", "속쓰림", "혈변"]):
        profile.update({
            "costTest": "20,000~200,000원\n혈액검사·소변검사·대변검사·복부초음파·필요 시 CT/내시경",
            "costTreat": "10,000~120,000원\n수액·약물치료·식이조절·원인별 처치",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    # 6. 가슴/호흡기/심장
    if _has_area(req, ["가슴·심장"]) or has(["가슴통증", "흉통", "호흡곤란", "숨참", "기침", "가래", "두근거림"]):
        profile.update({
            "costTest": "20,000~180,000원\n심전도·흉부 X-ray·혈액검사·심장효소검사·필요 시 심장초음파",
            "costTreat": "10,000~150,000원\n약물치료·흡입치료·응급 처치",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    # 7. 눈
    if _has_area(req, ["눈"]) or has(["시야", "시력", "눈통증", "충혈", "눈곱", "번쩍", "비문"]):
        profile.update({
            "costTest": "10,000~120,000원\n시력검사·안압검사·세극등검사·안저검사·필요 시 OCT",
            "costTreat": "10,000~100,000원\n점안약·약물치료·안과 처치",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    # 8. 유방
    if _has_area(req, ["유방"]):
        profile.update({
            "costTest": "30,000~200,000원\n유방초음파·유방촬영·필요 시 조직검사",
            "costTreat": "10,000~150,000원\n약물치료·염증 치료·시술/수술 상담",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    # 9. 비뇨기/골반/생식기
    if _has_area(req, ["생식기·비뇨기", "골반·사타구니"]):
        profile.update({
            "costTest": "10,000~150,000원\n소변검사·소변배양검사·초음파·성매개감염검사",
            "costTreat": "10,000~120,000원\n항생제·약물치료·원인별 처치",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    # 10. 턱·침샘/입·치아/목·인후/귀/코
    if _has_area(req, ["입·치아", "턱·침샘"]):
        profile.update({
            "costTest": "10,000~180,000원\n치과 X-ray·침샘초음파·필요 시 CT·배양검사",
            "costTreat": "10,000~150,000원\n약물치료·소독·치과/침샘 처치",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    if _has_area(req, ["목·인후", "귀", "코"]):
        profile.update({
            "costTest": "10,000~150,000원\n내시경검사·배양검사·청력검사·부비동 X-ray/CT",
            "costTreat": "10,000~100,000원\n약물치료·흡입/세척·이비인후과 처치",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    # 11. 항문·직장
    if _has_area(req, ["항문·직장"]):
        profile.update({
            "costTest": "20,000~180,000원\n항문경·직장수지검사·대변검사·필요 시 대장내시경",
            "costTreat": "10,000~150,000원\n약물치료·좌욕·외과 처치·시술 상담",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    # 12. 전신 증상
    if _has_area(req, ["전신·여러 부위"]) or has(["발열", "오한", "피로", "무기력", "몸살"]):
        profile.update({
            "costTest": "20,000~150,000원\n혈액검사·CRP/ESR·소변검사·흉부 X-ray·감염 검사",
            "costTreat": "10,000~120,000원\n수액·약물치료·원인별 치료",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐.",
        })
        return profile

    return profile


def apply_exam_cost_profile(req: AnalyzeRequest, raw: dict) -> dict:
    """기존 Claude/KCD 비용이 부위·증상과 충돌하지 않도록 최종 비용 문구를 덮어씁니다."""
    profile = build_exam_cost_profile(req, raw)
    for key, value in profile.items():
        raw[key] = value
    return raw

def _merge_with_rule_based(req: AnalyzeRequest, raw: dict | None) -> dict:
    """Claude 응답이 일부 비어 있을 때 rule-based 결과로 채웁니다."""
    base = build_rule_based_analysis(req)
    if not raw:
        return base
    for key, value in base.items():
        if key not in raw or raw[key] in (None, "", [], {}):
            raw[key] = value
    for k in ["dept1", "dept2", "dept3"]:
        if not isinstance(raw.get(k), dict) or not raw[k].get("name"):
            raw[k] = base[k]
    if not raw.get("predictedDiseases"):
        raw["predictedDiseases"] = base["predictedDiseases"]
    return _sanitize_gender_departments(req, raw)


async def analyze_symptoms(
    req: AnalyzeRequest,
    user_lat: float | None = None,
    user_lng: float | None = None,
) -> AnalyzeResponse:
    """
    증상 분석 메인 함수.
    Claude API가 설정되어 있으면 Claude 결과를 사용하되,
    API 키가 없거나 실패하면 rule-based 결과로 안전하게 반환합니다.
    """
    settings = get_settings()

    try:
        raw = await call_claude(req) if settings.ANTHROPIC_API_KEY else build_rule_based_analysis(req)
    except Exception as e:
        logger.warning(f"Claude 분석 실패 또는 미설정 — rule-based fallback 사용: {e}")
        raw = build_rule_based_analysis(req)

    raw = _merge_with_rule_based(req, raw)

    if req.is_urgent:
        raw["isUrgent"] = True
        if raw.get("urgencyText") == "낮음":
            raw["urgencyText"] = "높음"

    # 실제 병원명 후보 / HIRA 검색 결과 보강
    try:
        from app.services.hospital_service import build_frontend_hospitals, geocode_address

        departments = []
        for k in ["dept1", "dept2", "dept3"]:
            v = raw.get(k)
            if isinstance(v, dict) and v.get("name"):
                departments.append(v["name"])

        search_lat, search_lng = user_lat, user_lng
        if not (search_lat and search_lng) and req.region:
            coords = await geocode_address(req.region)
            if coords:
                search_lat = coords["lat"]
                search_lng = coords["lng"]

        raw["nearbyHospitals"] = await build_frontend_hospitals(
            region=req.region or "현재 위치 주변",
            departments=departments,
            user_lat=search_lat,
            user_lng=search_lng,
            limit_per_type=5,
        )
        logger.info(f"frontend 호환 병원 목록 생성: {len(raw['nearbyHospitals'])}건")
    except Exception as e:
        logger.warning(f"병원 목록 보강 실패: {e}")

    # KCD 기반 비용 보강
    try:
        from app.services.kcd_service import symptom_to_kcd, get_cost_by_kcd, assess_urgency
        symptom_text = req.symptom or " ".join(req.areas)
        kcd_matches = symptom_to_kcd(symptom_text, areas=req.areas, top_n=1)
        if kcd_matches:
            cost_data = get_cost_by_kcd(kcd_matches[0]["kcd"])
            if cost_data:
                raw["costClinic"] = cost_data.get("cost_clinic") or raw.get("costClinic", "")
                raw["costTest"] = cost_data.get("cost_test") or raw.get("costTest", "")
                raw["costTreat"] = cost_data.get("cost_treat") or raw.get("costTreat", "")
                raw["costInpat"] = cost_data.get("cost_inpat") or raw.get("costInpat", "")
        backend_urgency = assess_urgency(symptom_text, req.areas, req.is_urgent, req.duration)
        if raw.get("urgencyText") == "낮음" and backend_urgency != "낮음":
            raw["urgencyText"] = backend_urgency
    except Exception as e:
        logger.warning(f"KCD 비용/응급도 보강 실패: {e}")

    # 부위·증상 기반 검사/비용 최종 보정
    # KCD 비용이 너무 포괄적으로 잡히는 경우(예: 손목 저림인데 뇌 CT/MRI) 방지
    raw = apply_exam_cost_profile(req, raw)

    try:
        return AnalyzeResponse(**raw)
    except Exception as e:
        logger.warning(f"응답 스키마 검증 실패, 최소 응답 반환: {e}")
        fallback = build_rule_based_analysis(req)
        return AnalyzeResponse(
            isUrgent=fallback.get("isUrgent", req.is_urgent),
            areaText=fallback.get("areaText", ""),
            symptomText=fallback.get("symptomText", ""),
            urgencyText=fallback.get("urgencyText", "낮음"),
            predictedDiseases=[DiseaseInfo(**d) for d in fallback.get("predictedDiseases", [])],
            dept1=DeptInfo(**fallback["dept1"]),
            dept2=DeptInfo(**fallback["dept2"]),
            dept3=DeptInfo(**fallback["dept3"]),
            hospGuide=fallback.get("hospGuide", ""),
            memoSay=fallback.get("memoSay", []),
            memoAsk=fallback.get("memoAsk", []),
            routeBest=fallback.get("routeBest", ""),
            routeFast=fallback.get("routeFast", ""),
            routeCheap=fallback.get("routeCheap", ""),
            routePro=fallback.get("routePro", ""),
            costClinic=fallback.get("costClinic", ""),
            costTest=fallback.get("costTest", ""),
            costTreat=fallback.get("costTreat", ""),
            costInpat=fallback.get("costInpat", ""),
        )

# -----------------------------------------------------------------------------
# PATCH v10: HIRA-like realistic outpatient cost ranges
# -----------------------------------------------------------------------------
def build_exam_cost_profile(req: AnalyzeRequest, raw: dict | None = None) -> dict:
    """
    환자 안내용 예상 본인부담 범위.
    확정 청구액이 아니라 부위+증상+위험신호+검사종류 기준의 현실적 범위입니다.
    원칙:
    - 단순 의원 초진은 20만원대로 올리지 않음.
    - CT/MRI는 위험 신호 또는 명확한 정밀검사 필요 상황에서만 상한에 반영.
    - 피부/귀/코/목/손발 저림 등은 각각 맞는 기본검사를 우선 매칭.
    """
    areas = " ".join(req.areas or [])
    symptom = req.symptom or ""
    disease_text = " ".join([str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in (raw or {}).get("predictedDiseases", [])])
    dept_text = " ".join([
        (raw or {}).get("dept1", {}).get("name", "") if isinstance((raw or {}).get("dept1"), dict) else str((raw or {}).get("dept1", "")),
        (raw or {}).get("dept2", {}).get("name", "") if isinstance((raw or {}).get("dept2"), dict) else str((raw or {}).get("dept2", "")),
        (raw or {}).get("dept3", {}).get("name", "") if isinstance((raw or {}).get("dept3"), dict) else str((raw or {}).get("dept3", "")),
    ])
    text = f"{areas} {symptom} {disease_text} {dept_text} {req.disease or ''}"

    def has(words: list[str]) -> bool:
        return any(w in text for w in words)
    def area_has(words: list[str]) -> bool:
        return any(w in areas for w in words)
    def p(label, clinic_desc, exam_desc, treat_desc, inpat_desc, clinic, exam, treat, inpat):
        return {
            "label": label,
            "costClinic": f"{clinic[0]:,}~{clinic[1]:,}원\n{clinic_desc}",
            "costTest": f"{exam[0]:,}~{exam[1]:,}원\n{exam_desc}",
            "costTreat": f"{treat[0]:,}~{treat[1]:,}원\n{treat_desc}",
            "costInpat": "200,000원~\n입원 날짜에 따라 달라짐." if inpat[1] == 0 else f"{inpat[0]:,}~{inpat[1]:,}원/일\n{inpat_desc}",
            "routeBest": f"{label} 기준으로 1차 진료 후 필요한 검사만 단계적으로 진행합니다.",
            "routeFast": f"{label} 증상이 빠르게 진행되거나 불편감이 크면 전문 진료기관에서 당일 검사와 처치를 고려합니다.",
            "routeCheap": "경증이면 의원 진찰 후 기본검사와 약 처방 중심으로 비용 부담을 줄입니다.",
            "routePro": "반복·악화·중증 소견이 있으면 전문병원 또는 대학병원 정밀검사를 고려합니다.",
        }

    red_neuro = has(["말이 어눌", "말 어눌", "발음 이상", "의식저하", "의식 변화", "실신", "경련", "한쪽 마비", "편측마비", "갑작스러운 마비", "극심한 두통", "최악의 두통", "벼락두통"])
    red_cardio = has(["흉통", "가슴압박", "쥐어짜", "방사통", "식은땀", "호흡곤란", "숨이 차", "청색증", "심근경색", "협심증"])
    red_eye = has(["갑작스러운 시야", "커튼", "시야가림", "시야결손", "번쩍임", "망막", "극심한 눈통증"])

    skin_sym = has(["가려움", "분비물", "진물", "고름", "발진", "홍반", "두드러기", "습진", "무좀", "각질", "비듬", "피부염", "상처 감염", "종기", "물집", "농가진"]) or area_has(["피부"])
    neuro_sym = has(["저림", "마비", "찌릿", "감각저하", "감각 이상", "근력저하", "힘 빠짐", "손저림", "발저림", "신경통"])
    trauma_sym = has(["접질", "삐었", "삐끗", "외상", "타박", "넘어", "부딪", "골절", "염좌", "인대", "부종", "붓기", "심한 통증", "운동 제한", "관절통"])
    infection_sym = has(["열", "발열", "오한", "고열", "몸살", "염증", "감염"])

    is_head = area_has(["머리·두피"]) or has(["두통", "편두통", "어지러움"])
    is_eye = area_has(["눈"]) or has(["시야", "시력", "충혈", "눈곱", "눈통증", "안구"])
    is_ear = area_has(["귀"]) or has(["귀", "외이도", "중이염", "이명", "청력", "먹먹"])
    is_nose = area_has(["코"]) or has(["코막힘", "콧물", "비염", "부비동", "후비루"])
    is_throat = area_has(["목·인후"]) or has(["인후통", "편도", "목소리", "쉰 목", "삼킴통증", "가래"])
    is_resp = has(["기침", "가래", "폐렴", "천식", "숨참", "호흡기"])
    is_hand = area_has(["손목·손가락", "팔·손"])
    is_foot = area_has(["다리·발", "발목·발가락"])
    is_back = area_has(["등·허리"])
    is_joint = area_has(["무릎", "어깨", "손목·손가락", "팔·손", "다리·발", "발목·발가락", "등·허리"])

    if (is_head and red_neuro) or red_neuro:
        return p("신경계 응급 감별", "응급도 평가·신경학적 진찰", "뇌 CT·혈액검사·필요 시 MRI", "응급 처치·전원/입원 평가", "필요 시 응급실/입원", [10000,30000], [80000,500000], [30000,250000], [200000,600000])
    if red_cardio or area_has(["가슴·심장"]):
        return p("심혈관·호흡기 응급 감별", "응급도 평가·활력징후 확인", "심전도·흉부 X-ray·심장효소/혈액검사", "약물·산소·전원/시술 가능", "중증 시 입원 가능", [10000,30000], [30000,200000], [30000,300000], [200000,600000])
    if red_eye or (is_eye and has(["시야", "번쩍", "커튼", "심한 통증"])):
        return p("급성 안과 증상", "안과 초진·시력 확인", "시력검사·안압검사·안저검사·OCT", "점안약·응급 안과 처치", "수술/응급 처치 시 가능", [5000,20000], [30000,180000], [10000,180000], [0,300000])

    if is_ear and has(["분비물", "진물", "고름", "가려움", "먹먹", "청력", "이명"]):
        return p("귀 질환 감별", "이비인후과 의원 진찰", "이경검사·고막검사·분비물 배양검사·청력검사", "약물 처방·귀 세척/소독·점이액", "대부분 외래 치료", [5000,20000], [5000,70000], [5000,50000], [0,0])

    if skin_sym:
        if neuro_sym and (is_hand or is_foot):
            return p("피부염·감염과 말초신경 증상 동반", "피부/감각/근력 확인", "피부확대경·진균검사·세균배양검사·염증검사·필요 시 신경전도검사", "외용약·소독·드레싱·항생제/항진균제·신경 증상 추적", "대부분 외래 치료", [5000,20000], [15000,150000], [10000,100000], [0,0])
        return p("피부염·진균감염·상처감염 의심", "피부 진찰·문진", "피부확대경·진균검사·세균배양검사·필요 시 조직검사", "외용약·소독·드레싱·항생제/항진균제", "대부분 외래 치료", [5000,15000], [10000,80000], [10000,80000], [0,0])

    if (is_hand or is_foot) and neuro_sym:
        return p("말초신경/압박신경 이상 의심", "감각·근력·관절 진찰", "신경전도검사·근전도·부위 X-ray·초음파", "약물·보조기·주사·물리치료", "대부분 외래 치료", [5000,20000], [30000,180000], [20000,120000], [0,0])
    if (is_back or area_has(["목·인후"]) or is_joint) and neuro_sym:
        return p("척추/말초신경 압박 감별", "신경학적 진찰·자세/통증 평가", "척추 또는 관절 X-ray·신경전도검사·필요 시 MRI", "약물·주사·물리치료·재활", "진행성 마비/수술 시 가능", [5000,20000], [30000,250000], [20000,180000], [0,300000])
    if is_joint and trauma_sym:
        exam = "손/손목 X-ray·초음파·필요 시 MRI" if is_hand else "발/발목 X-ray·초음파·필요 시 MRI" if is_foot else "관절/척추 X-ray·초음파·필요 시 MRI"
        return p("근골격계 손상·관절질환 의심", "관절·근육 진찰", exam, "부목·보조기·약물·주사·물리치료", "골절·수술 필요 시 가능", [5000,15000], [10000,150000], [10000,120000], [0,300000])

    if is_nose:
        return p("비염·부비동염 등 코 질환 의심", "이비인후과 의원 진찰", "비강내시경·알레르기검사·부비동 X-ray·필요 시 CT", "약물·비강세척·분무제 치료", "대부분 외래 치료", [5000,20000], [10000,120000], [5000,60000], [0,0])
    if is_throat:
        return p("인후염·편도염·후두질환 의심", "이비인후과/내과 진찰", "인후검사·후두내시경·신속항원검사·배양검사", "약물·소독/흡입·수액 필요 시", "대부분 외래 치료", [5000,20000], [5000,70000], [5000,70000], [0,0])
    if is_resp:
        return p("호흡기 질환 감별", "호흡기/내과 진찰", "흉부 X-ray·폐기능검사·염증검사", "약물·흡입치료·수액 필요 시", "폐렴/호흡곤란 시 가능", [5000,20000], [10000,100000], [5000,80000], [0,300000])

    if area_has(["배·소화기"]) or has(["복통", "소화불량", "속쓰림", "설사", "구토", "혈변", "흑변", "장염", "위염", "담낭", "맹장", "충수"]):
        severe = has(["혈변", "흑변", "심한 복통", "우상복부", "황달", "맹장", "충수"])
        return p("소화기 질환 의심", "복부 진찰·문진", "혈액검사·소변/대변검사·복부초음파·필요 시 CT/내시경" if severe else "혈액검사·소변/대변검사·복부초음파·필요 시 내시경", "수액·약물·식이조절·원인별 처치", "탈수·급성 복증·수술 시 가능", [5000,20000], [20000,250000] if severe else [10000,150000], [10000,120000], [0,400000])

    if is_eye:
        return p("안과 질환 의심", "안과 초진·시력 확인", "시력검사·세극등검사·안압검사·안저검사", "점안약·약물·안과 처치", "대부분 외래 치료", [5000,15000], [10000,80000], [10000,70000], [0,0])
    if is_head:
        return p("단순 두통·어지러움 감별", "신경학적 진찰·혈압 확인", "혈압 확인·신경학적 문진·필요 시 혈액검사", "진통제·생활요법·필요 시 예방약", "대부분 외래 치료", [5000,20000], [5000,80000], [5000,60000], [0,0])
    if area_has(["유방"]) or has(["유방", "멍울", "유두분비", "유방통", "유선염"]):
        return p("유방 질환 감별", "유방 진찰·문진", "유방초음파·유방촬영·필요 시 조직검사", "약물·배농·추적검사·수술 상담", "시술/수술 시 가능", [7000,20000], [30000,200000], [10000,150000], [0,300000])
    if area_has(["생식기·비뇨기", "골반·사타구니"]) or has(["소변", "혈뇨", "배뇨통", "빈뇨", "방광염", "요로감염", "요로결석", "질분비물", "성병", "골반통", "전립선"]):
        severe = has(["요로결석", "옆구리 통증", "혈뇨", "고열", "신우신염"])
        return p("비뇨기·골반 질환 감별", "비뇨기/골반 진찰", "소변검사·배양검사·초음파·필요 시 CT" if severe else "소변검사·배양검사·초음파·성매개감염검사", "항생제·약물·수액·결석 처치", "감염/결석 심할 때 가능", [5000,20000], [10000,200000] if severe else [10000,120000], [10000,120000], [0,300000])
    if area_has(["턱·침샘", "입·치아"]) or has(["침샘", "타석", "턱밑", "치통", "잇몸", "구강", "입안", "치아"]):
        return p("구강·침샘 질환 감별", "구강·침샘 진찰", "치과 X-ray·침샘초음파·필요 시 CT·배양검사", "구강 처치·항생제·치과/침샘 처치", "감염 심할 때 가능", [5000,20000], [10000,180000], [10000,150000], [0,300000])
    if area_has(["항문·직장"]) or has(["항문", "직장", "치질", "치핵", "혈변", "배변", "항문통증"]):
        return p("항문·직장 질환 감별", "항문 진찰·문진", "항문경·직장수지검사·대변검사·필요 시 대장내시경", "좌욕·약물·외과 처치·시술 상담", "수술 시 가능", [5000,15000], [10000,150000], [10000,150000], [0,300000])
    if area_has(["전신·여러 부위", "잘 모르겠음"]) or infection_sym or has(["피로", "무기력", "근육통"]):
        return p("전신 감염·염증 감별", "전신 진찰·활력징후 확인", "혈액검사·CRP/ESR·소변검사·흉부 X-ray", "수액·약물·항생제 여부 판단", "고열/중증 시 가능", [5000,20000], [20000,120000], [10000,100000], [0,300000])

    return p("일반 초진·기본 검사", "초진 문진·기본 진찰", "혈액검사·소변검사 등", "약물 처방·생활관리", "필요 시", [5000,15000], [10000,60000], [5000,60000], [0,0])


def apply_exam_cost_profile(req: AnalyzeRequest, raw: dict) -> dict:
    profile = build_exam_cost_profile(req, raw)
    for key in ["costClinic", "costTest", "costTreat", "costInpat", "routeBest", "routeFast", "routeCheap", "routePro"]:
        if profile.get(key):
            raw[key] = profile[key]
    return raw


# ═══════════════════════════════════════
# v11 비용 엔진: HIRA 원자료 연동 전 검사 종류 기반 예상 본인부담 범위
# - 전국보건기관표준데이터는 기관정보용이며 비용 직접 근거가 아님.
# - HIRA 세부 수가/비급여 데이터 연동 전까지, 검사군별 안내 범위로 산정.
# - 총액을 낮추거나 올리는 것이 아니라 진찰/검사/처치/입원 항목을 분리하여 표시.
# ═══════════════════════════════════════

def build_exam_cost_profile(req: AnalyzeRequest, raw: dict | None = None) -> dict:  # type: ignore[no-redef]
    raw = raw or {}
    disease_text = " ".join([
        (d.get("name", "") + " " + d.get("reason", ""))
        for d in raw.get("predictedDiseases", []) if isinstance(d, dict)
    ])
    dept_text = " ".join([
        raw.get(k, {}).get("name", "") + " " + raw.get(k, {}).get("reason", "")
        for k in ["dept1", "dept2", "dept3"] if isinstance(raw.get(k), dict)
    ])
    text = f"{req.symptom or ''} {' '.join(req.areas or [])} {disease_text} {dept_text} {req.disease or ''}".lower()

    def has(words: list[str]) -> bool:
        return _contains_any(text, words)

    def p(cost_clinic, cost_test, cost_treat, cost_inpat, route_best, route_fast="증상이 빠르게 악화되거나 고위험 신호가 있으면 전문 병원 또는 응급 진료를 고려합니다."):
        # 입원비는 실제 입원 일수·병원급·병실·처치에 따라 달라지므로 상한을 고정하지 않습니다.
        # 화면에는 하한 예상 비용만 표시하고, 설명에는 날짜에 따라 달라짐을 표시합니다.
        admission_text = "200,000원~\n입원 날짜에 따라 달라짐."
        return {
            "costClinic": cost_clinic,
            "costTest": cost_test,
            "costTreat": cost_treat,
            "costInpat": admission_text,
            "routeBest": route_best,
            "routeFast": route_fast,
            "routeCheap": "1차 의원에서 기본 진료 후 꼭 필요한 검사만 진행해 비용 부담을 줄입니다.",
            "routePro": "반복·만성·고위험 증상은 해당 전문과 또는 대학 병원 협진을 고려합니다.",
            "costSourceNote": "예상 본인부담 안내값입니다. 실제 비용은 검사 종류, 기관급, 급여/비급여 여부, 실손보험에 따라 달라집니다. HIRA 세부 수가 연동 전 임시 기준표입니다.",
        }

    skin_words = ["가려움", "발진", "홍반", "분비물", "진물", "고름", "물집", "두드러기", "각질", "습진", "피부염", "무좀", "진균", "농가진"]
    trauma_words = ["접질", "삐", "외상", "넘어", "부딪", "타박", "골절", "염좌", "부종", "붓", "운동 제한", "움직일 때", "통증"]
    numb_words = ["저림", "마비", "감각저하", "찌릿", "전기", "힘빠", "근력", "손저림", "발저림"]
    neuro_red = ["의식저하", "말 어눌", "발음 이상", "편측마비", "한쪽 마비", "경련", "실신", "벼락두통", "갑자기 심한 두통", "극심한 두통"]
    cardio_red = ["흉통", "가슴통증", "호흡곤란", "숨이 안", "식은땀", "청색증"]
    eye_red = ["시야결손", "번쩍", "커튼", "시야가 가려", "갑자기 안 보", "복시"]

    upper = _has_area(req, ["팔·손", "손목·손가락"])
    lower = _has_area(req, ["다리·발", "발목·발가락", "무릎"])
    limb = upper or lower or _has_area(req, ["어깨"])
    skin = has(skin_words)
    trauma = has(trauma_words)
    numb = has(numb_words)

    if has(neuro_red):
        return p(
            "10,000~30,000원", "80,000~500,000원\n뇌 CT·혈액검사·필요 시 MRI", "30,000~250,000원\n응급 처치·전원/입원 평가", "200,000원~\n입원 날짜에 따라 달라짐.",
            "신경계 위험 신호가 있어 응급도 평가와 뇌영상 검사를 우선 고려합니다.",
            "의식저하·말 어눌함·편측 마비·경련이 있으면 즉시 응급실로 이동합니다.",
        )
    if has(cardio_red):
        return p(
            "10,000~30,000원", "30,000~220,000원\n심전도·흉부 X-ray·심장효소/혈액검사", "30,000~300,000원\n약물·산소·전원/시술 가능", "200,000원~\n입원 날짜에 따라 달라짐.",
            "심혈관·호흡기 위험 신호가 있어 활력징후와 응급 검사를 우선 고려합니다.",
        )
    if _has_area(req, ["눈"]) and has(eye_red):
        return p(
            "5,000~20,000원", "30,000~180,000원\n시력검사·안압검사·안저검사·OCT", "10,000~180,000원\n점안약·응급 안과 처치", "0~300,000원\n수술/응급 처치 시 가능",
            "급성 시야 이상은 안과에서 안저/OCT 등 정밀 검사를 우선 고려합니다.",
        )

    if _has_area(req, ["귀"]):
        return p(
            "5,000~20,000원", "5,000~70,000원\n이경검사·고막검사·분비물 배양검사·필요 시 청력검사", "5,000~50,000원\n점이액·약물 처방·귀 세척/소독", "0원\n대부분 외래 치료",
            "이비인후과에서 외이도·고막 상태를 먼저 확인하고 필요한 경우 분비물 배양이나 청력검사를 진행합니다.",
        )

    if skin:
        if limb and numb:
            return p(
                "5,000~20,000원", "15,000~150,000원\n피부확대경·진균검사·세균배양검사·염증검사·필요 시 신경전도검사", "10,000~100,000원\n외용약·소독·드레싱·항생제/항진균제·신경 증상 추적", "0원\n대부분 외래 치료",
                "피부 병변을 우선 확인하되 저림이 지속되면 말초신경 검사를 추가합니다.",
            )
        return p(
            "5,000~15,000원", "10,000~80,000원\n피부확대경·진균검사·세균배양검사·필요 시 조직검사", "10,000~80,000원\n외용약·소독·드레싱·항생제/항진균제", "0원\n대부분 외래 치료",
            "피부과에서 병변 형태를 확인한 뒤 필요한 검사만 단계적으로 진행합니다.",
        )

    if upper and numb:
        symptom_only = (req.symptom or "").lower()
        if "손가락" in symptom_only and "손목" not in symptom_only:
            exam_label = "손가락 X-ray·초음파·필요 시 신경전도검사"
            reason_label = "손가락 증상은 손가락 관절/힘줄 문제와 말초신경 이상을 구분해 확인합니다."
        elif "손목" in symptom_only:
            exam_label = "신경전도검사·근전도·손목 X-ray·초음파"
            reason_label = "손목 저림은 손목터널증후군과 말초신경 압박을 먼저 확인합니다."
        else:
            exam_label = "신경전도검사·근전도·손/손목 X-ray·초음파"
            reason_label = "손 저림은 말초신경 압박과 관절 문제를 먼저 확인합니다."
        return p(
            "5,000~20,000원", f"30,000~180,000원\n{exam_label}", "20,000~120,000원\n보조기·약물·주사·물리치료", "0원\n대부분 외래 치료",
            reason_label,
        )
    if lower and numb:
        return p(
            "5,000~20,000원", "30,000~250,000원\n신경전도검사·근전도·하지/요추 X-ray·필요 시 MRI", "20,000~180,000원\n약물·주사·물리치료·재활", "0~300,000원\n진행성 마비/수술 시 가능",
            "하지 저림은 말초신경 이상과 허리 신경 압박 가능성을 함께 확인합니다.",
        )
    if limb and trauma:
        exam = "손/손목 X-ray·초음파·필요 시 MRI" if upper else "발/발목/무릎 X-ray·초음파·필요 시 MRI"
        return p(
            "5,000~15,000원", f"10,000~150,000원\n{exam}", "10,000~120,000원\n부목·보조기·약물·주사·물리치료", "0~300,000원\n골절·수술 필요 시 가능",
            "정형외과에서 골절·염좌·힘줄 손상 여부를 먼저 확인합니다.",
        )

    if _has_area(req, ["코"]) or has(["콧물", "코막힘", "후비루", "비염", "축농증", "부비동"]):
        sinus = has(["누런 콧물", "얼굴통증", "부비동", "축농증", "심한 코막힘"])
        return p(
            "5,000~20,000원", ("10,000~180,000원\n비강내시경·부비동 X-ray·필요 시 CT" if sinus else "10,000~80,000원\n비강내시경·알레르기검사·부비동 X-ray"), "5,000~60,000원\n약물·비강세척·분무제 치료", "0원\n대부분 외래 치료",
            "이비인후과에서 비강 상태를 확인하고 부비동염 의심 시 영상검사를 고려합니다.",
        )
    if _has_area(req, ["목·인후"]) or has(["인후통", "삼킴통증", "편도", "후두", "쉰목소리", "목아픔"]):
        return p(
            "5,000~20,000원", "5,000~70,000원\n인후검사·후두내시경·신속항원검사·배양검사", "5,000~70,000원\n약물·소독/흡입·수액 필요 시", "0원\n대부분 외래 치료",
            "목·인후 증상은 이비인후과 또는 내과에서 인후/후두 상태를 먼저 확인합니다.",
        )
    if has(["기침", "가래", "폐렴", "천식", "호흡기"]):
        return p(
            "5,000~20,000원", "10,000~120,000원\n흉부 X-ray·폐기능검사·염증검사", "5,000~80,000원\n약물·흡입치료·수액 필요 시", "0~300,000원\n폐렴/호흡곤란 시 가능",
            "호흡기 증상은 흉부 X-ray와 염증검사를 필요한 범위에서 진행합니다.",
        )
    if _has_area(req, ["배·소화기"]) or has(["복통", "소화불량", "속쓰림", "설사", "구토", "혈변", "흑변", "장염", "위염", "담낭", "맹장", "충수"]):
        severe = has(["혈변", "흑변", "심한 복통", "우상복부", "황달", "맹장", "충수", "체중감소"])
        return p(
            "5,000~20,000원", ("20,000~250,000원\n혈액검사·소변/대변검사·복부초음파·필요 시 CT/내시경" if severe else "10,000~150,000원\n혈액검사·소변/대변검사·복부초음파·필요 시 내시경"), "10,000~120,000원\n수액·약물·식이조절·원인별 처치", "0~400,000원\n탈수·급성 복증·수술 시 가능",
            "소화기 증상은 기본 검사 후 복부초음파·내시경·CT 필요성을 단계적으로 판단합니다.",
        )
    if _has_area(req, ["눈"]):
        return p("5,000~15,000원", "10,000~80,000원\n시력검사·세극등검사·안압검사·안저검사", "10,000~70,000원\n점안약·약물·안과 처치", "0원\n대부분 외래 치료", "안과에서 기본 시력/세극등/안압 검사를 우선 진행합니다.")
    if _has_area(req, ["머리·두피"]) or has(["두통", "편두통", "어지러움"]):
        return p("5,000~20,000원", "5,000~80,000원\n혈압 확인·신경학적 문진·필요 시 혈액검사", "5,000~60,000원\n진통제·생활요법·필요 시 예방약", "0원\n대부분 외래 치료", "위험 신호가 없는 두통은 기본 진찰과 혈액검사 범위에서 먼저 확인합니다.")
    if _has_area(req, ["유방"]):
        return p("7,000~20,000원", "30,000~220,000원\n유방초음파·유방촬영·필요 시 조직검사", "10,000~150,000원\n약물·배농·추적검사·수술 상담", "0~300,000원\n시술/수술 시 가능", "유방 증상은 초음파/촬영 후 조직검사 필요성을 판단합니다.")
    if _has_area(req, ["생식기·비뇨기", "골반·사타구니", "옆구리"]) or has(["소변", "혈뇨", "배뇨통", "빈뇨", "방광염", "요로감염", "요로결석", "질분비물", "성병", "골반통", "전립선"]):
        stone = has(["요로결석", "옆구리 통증", "혈뇨", "고열", "신우신염"])
        return p("5,000~20,000원", ("10,000~200,000원\n소변검사·배양검사·초음파·필요 시 CT" if stone else "10,000~120,000원\n소변검사·배양검사·초음파·성매개감염검사"), "10,000~120,000원\n항생제·약물·수액·결석 처치", "0~300,000원\n감염/결석 심할 때 가능", "비뇨기/골반 증상은 소변검사와 배양검사를 기본으로 초음파/CT 필요성을 판단합니다.")
    if _has_area(req, ["턱·침샘", "입·치아"]):
        return p("5,000~20,000원", "10,000~180,000원\n치과 X-ray·침샘초음파·필요 시 CT·배양검사", "10,000~150,000원\n구강 처치·항생제·치과/침샘 처치", "0~300,000원\n감염 심할 때 가능", "구강·침샘 증상은 X-ray/초음파 후 CT나 배양검사 필요성을 판단합니다.")
    if _has_area(req, ["항문·직장"]):
        return p("5,000~15,000원", "10,000~150,000원\n항문경·직장수지검사·대변검사·필요 시 대장내시경", "10,000~150,000원\n좌욕·약물·외과 처치·시술 상담", "0~300,000원\n수술 시 가능", "항문·직장 증상은 진찰과 항문경/내시경 필요성을 단계적으로 판단합니다.")
    if _has_area(req, ["전신·여러 부위", "잘 모르겠음"]) or has(["발열", "오한", "고열", "몸살", "피로", "무기력", "근육통"]):
        return p("5,000~20,000원", "20,000~120,000원\n혈액검사·CRP/ESR·소변검사·흉부 X-ray", "10,000~100,000원\n수액·약물·항생제 여부 판단", "0~300,000원\n고열/중증 시 가능", "전신 증상은 염증수치와 감염 여부를 기본으로 확인합니다.")
    return p("5,000~15,000원", "10,000~60,000원\n혈액검사·소변검사 등", "5,000~60,000원\n약물 처방·생활관리", "0원\n필요 시", "일반 초진 후 필요한 검사만 단계적으로 진행합니다.")


# ═══════════════════════════════════════
# v12 memo/question patch: 부위·증상별 병원 메모와 질문을 세분화
# ═══════════════════════════════════════

def build_memo_question_profile(req: AnalyzeRequest, raw: dict | None = None) -> dict:
    raw = raw or {}
    disease_text = " ".join([
        (d.get("name", "") + " " + d.get("reason", ""))
        for d in raw.get("predictedDiseases", []) if isinstance(d, dict)
    ])
    dept_text = " ".join([
        raw.get(k, {}).get("name", "") + " " + raw.get(k, {}).get("reason", "")
        for k in ["dept1", "dept2", "dept3"] if isinstance(raw.get(k), dict)
    ])
    text = f"{req.symptom or ''} {' '.join(req.areas or [])} {disease_text} {dept_text}".lower()

    def has(words: list[str]) -> bool:
        return _contains_any(text, words)

    def pack(say, ask):
        return {"memoSay": say[:5], "memoAsk": ask[:4]}

    if _has_area(req, ["배·소화기"]) or has(["구토", "구역", "더부룩", "속쓰림", "복통", "설사", "변비", "혈변", "흑변", "소화불량"]):
        return pack(
            ["구토 횟수와 지속 시간", "식전/식후 증상 변화", "속쓰림·신트림 여부", "설사·변비·혈변/흑변 여부", "최근 음식·음주·복용약"],
            ["내시경이나 복부초음파가 필요한가요?", "탈수나 수액 치료가 필요한가요?", "피해야 할 음식과 약은 무엇인가요?", "다시 방문해야 하는 위험 신호는 무엇인가요?"]
        )
    if _has_area(req, ["귀"]):
        return pack(
            ["분비물 색·냄새·양", "귀 통증·먹먹함·청력저하 여부", "가려움 시작 시점", "면봉·이어폰 사용 여부", "발열 또는 어지러움 여부"],
            ["외이도염이나 중이염 가능성이 있나요?", "고막검사나 청력검사가 필요한가요?", "귀 세척이나 점이액이 필요한가요?", "물 접촉을 피해야 하나요?"]
        )
    if _has_area(req, ["코"]):
        return pack(
            ["콧물 색과 코막힘 정도", "재채기·코 가려움 여부", "후비루·얼굴 통증 여부", "증상이 심한 시간대/환경", "알레르기 병력과 복용약"],
            ["비염과 부비동염 중 어느 쪽에 가까운가요?", "비강내시경이나 알레르기검사가 필요한가요?", "항생제나 분무제가 필요한가요?", "생활환경에서 줄일 요인은 무엇인가요?"]
        )
    if _has_area(req, ["목·인후"]):
        return pack(
            ["인후통·삼킴통증 정도", "기침·가래·후비루 여부", "목소리 변화 여부", "발열 여부와 체온", "최근 감기/접촉력과 복용약"],
            ["편도염/인후염/후두염 중 어느 쪽인가요?", "신속항원검사나 후두내시경이 필요한가요?", "항생제가 필요한 상황인가요?", "악화 시 언제 재진해야 하나요?"]
        )
    if _has_area(req, ["피부"]) or has(["가려움", "발진", "홍반", "두드러기", "진물", "분비물", "물집", "각질", "피부염"]):
        return pack(
            ["발진 위치와 퍼진 범위", "가려움/통증/진물 여부", "새로 쓴 화장품·세제·약", "사진 변화와 시작일", "알레르기·아토피 병력"],
            ["피부염/두드러기/감염 중 무엇이 의심되나요?", "진균검사나 배양검사가 필요한가요?", "바르는 약과 먹는 약을 어떻게 써야 하나요?", "전염 가능성이나 주의사항이 있나요?"]
        )
    if _has_area(req, ["손목·손가락", "팔·손"]) and has(["저림", "마비", "감각", "찌릿", "힘"]):
        symptom_only = (req.symptom or "").lower()
        if "손목" in symptom_only:
            return pack(
                ["저림 위치와 지속 시간", "손목 통증·힘 빠짐 여부", "반복 작업/자세/외상 여부", "야간 악화 여부", "목·어깨 통증 동반 여부"],
                ["손목터널증후군 가능성이 있나요?", "신경전도검사나 근전도가 필요한가요?", "보조기나 물리치료가 필요한가요?", "수술 평가가 필요한 기준은 무엇인가요?"]
            )
        return pack(
            ["손가락 저림/통증 위치", "감각저하·힘 빠짐 여부", "반복 작업/외상 여부", "붓기·열감 동반 여부", "목·어깨 통증 동반 여부"],
            ["손가락 관절이나 힘줄 문제인가요?", "신경검사가 필요한 증상인가요?", "X-ray나 초음파가 필요한가요?", "보조기나 물리치료가 필요한가요?"]
        )
    if _has_area(req, ["다리·발", "발목·발가락", "무릎"]) and has(["저림", "마비", "감각", "찌릿", "힘"]):
        return pack(
            ["저림/통증 위치와 범위", "보행 불편·힘 빠짐 여부", "허리 통증 동반 여부", "증상 악화 자세", "당뇨/혈관질환 여부"],
            ["말초신경 문제인지 허리 신경 문제인지요?", "신경전도검사나 영상검사가 필요한가요?", "재활치료나 약물치료가 필요한가요?", "응급으로 봐야 할 증상은 무엇인가요?"]
        )
    if _has_area(req, ["다리·발", "발목·발가락", "무릎", "손목·손가락", "팔·손", "어깨", "등·허리"]):
        return pack(
            ["다친 시점과 원인", "통증 위치와 움직임 제한", "붓기·멍·열감 여부", "걷기/사용 가능 여부", "이전 같은 부위 손상 병력"],
            ["X-ray나 초음파가 필요한가요?", "골절/인대손상 가능성이 있나요?", "부목이나 보조기가 필요한가요?", "운동/일상 복귀 시점은 언제인가요?"]
        )
    if _has_area(req, ["눈"]):
        return pack(
            ["시야 이상 양상과 시작 시점", "통증·충혈·눈곱 여부", "한쪽/양쪽 여부", "렌즈 착용 여부", "두통·어지러움 동반 여부"],
            ["안압검사나 안저검사가 필요한가요?", "망막/각막 문제 가능성이 있나요?", "점안약 사용 방법은 어떻게 되나요?", "응급으로 가야 할 변화는 무엇인가요?"]
        )
    if _has_area(req, ["머리·두피"]):
        return pack(
            ["두통 위치와 강도", "갑자기 시작했는지 여부", "시야 이상·마비·말 어눌함 여부", "구토/어지러움 동반 여부", "진통제 복용과 효과"],
            ["단순 두통인지 정밀검사가 필요한지요?", "CT/MRI가 필요한 위험 신호가 있나요?", "복용할 약과 피해야 할 약은 무엇인가요?", "재발 예방 방법은 무엇인가요?"]
        )
    if _has_area(req, ["가슴·심장"]):
        return pack(
            ["통증 위치·양상·지속 시간", "숨참·식은땀·방사통 여부", "운동/휴식과의 관계", "혈압·심장질환 병력", "복용 중인 약"],
            ["심전도와 심장효소검사가 필요한가요?", "응급실로 가야 하는 기준은 무엇인가요?", "운동이나 카페인을 피해야 하나요?", "추적 진료가 필요한가요?"]
        )
    if _has_area(req, ["생식기·비뇨기", "골반·사타구니", "옆구리"]):
        return pack(
            ["배뇨통·빈뇨·혈뇨 여부", "분비물 색과 냄새", "옆구리/골반 통증 여부", "발열 여부", "성별·임신 가능성·복용약"],
            ["소변검사나 배양검사가 필요한가요?", "항생제가 필요한 상황인가요?", "초음파나 CT가 필요한가요?", "파트너 검사나 생활 주의가 필요한가요?"]
        )
    if _has_area(req, ["유방"]):
        return pack(
            ["멍울 위치와 크기 변화", "통증·열감·발적 여부", "유두 분비물 여부", "생리주기와 관련성", "가족력/과거 검사 이력"],
            ["유방초음파나 촬영이 필요한가요?", "조직검사가 필요한 소견인가요?", "유선염 가능성이 있나요?", "추적검사 간격은 어떻게 되나요?"]
        )
    if _has_area(req, ["항문·직장"]):
        return pack(
            ["출혈 색과 양", "배변 시 통증 여부", "변비/설사와 배변 습관 변화", "튀어나오는 느낌 여부", "복용약과 과거 대장검사 이력"],
            ["항문경이나 대장내시경이 필요한가요?", "치핵/열상/염증 중 무엇이 의심되나요?", "약물/좌욕/시술 중 어떤 치료가 맞나요?", "응급 진료가 필요한 출혈 기준은 무엇인가요?"]
        )
    return pack(
        ["증상 시작일과 변화", "가장 불편한 증상", "발열 여부와 체온", "복용 중인 약 이름", "기저질환과 최근 검사 이력"],
        ["어떤 원인이 가장 의심되나요?", "필요한 검사는 무엇인가요?", "처방 약 복용 기간은 어떻게 되나요?", "다시 진료받아야 할 기준은 무엇인가요?"]
    )

_previous_apply_exam_cost_profile_v12 = apply_exam_cost_profile

def apply_exam_cost_profile(req: AnalyzeRequest, raw: dict) -> dict:  # type: ignore[no-redef]
    raw = _previous_apply_exam_cost_profile_v12(req, raw)
    memo = build_memo_question_profile(req, raw)
    raw["memoSay"] = memo["memoSay"]
    raw["memoAsk"] = memo["memoAsk"]
    return raw
