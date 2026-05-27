"""
KCD 코드 매핑 서비스 (STEP 3 — v2)
- 부위 우선 → 증상 세분화 매핑
- 진료 필요도 자동 판정
- 부위별 검사 항목 및 비용
- 근골격계 + 소화기계 + 주요 부위 전체 커버
"""

import logging
import re
from typing import Optional

logger = logging.getLogger("mediroute.kcd")


# ═══════════════════════════════════════
#  KCD 매핑 마스터 테이블
# ═══════════════════════════════════════

KCD_MASTER: dict[str, dict] = {
    # ── 근골격계 ──
    "M17": {
        "name": "무릎관절증 (Gonarthrosis)",
        "keywords": {"무릎", "통증", "계단", "시림", "관절", "연골", "퇴행성"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "X-ray 5,000~15,000원 / MRI 100,000~300,000원",
        "cost_treat": "주사치료 30,000~80,000원 / 물리치료 10,000~20,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M51": {
        "name": "기타 추간판장애 (허리 디스크)",
        "keywords": {"허리", "요통", "디스크", "다리저림", "좌골", "허리통증"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~30,000원",
        "cost_test": "X-ray 5,000~15,000원 / MRI 150,000~350,000원",
        "cost_treat": "신경차단술 50,000~150,000원 / 물리치료 10,000~20,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M75": {
        "name": "어깨 병변 (오십견/회전근개)",
        "keywords": {"어깨", "팔", "올림", "오십견", "회전근개"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "초음파 30,000~80,000원 / MRI 150,000~300,000원",
        "cost_treat": "주사 30,000~100,000원 / 체외충격파 30,000~50,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M50": {
        "name": "경추간판장애 (목 디스크)",
        "keywords": {"경추", "목디스크", "팔저림", "뒷목"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "X-ray 5,000~15,000원 / MRI 150,000~350,000원",
        "cost_treat": "신경차단술 50,000~150,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "G56": {
        "name": "손목터널증후군",
        "keywords": {"손목", "손저림", "손목터널", "수근관", "저림"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "X-ray 5,000~15,000원 / 근전도 30,000~60,000원",
        "cost_treat": "주사 30,000~50,000원 / 보조기 20,000~50,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M65W": {
        "name": "손목 건초염",
        "keywords": {"손목", "손목통증", "붓기", "부종", "통증", "반복사용"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "손목 X-ray 5,000~20,000원 / 초음파 30,000~80,000원",
        "cost_treat": "약물·보조기·주사치료 10,000~80,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "S63W": {
        "name": "손목 염좌 또는 인대 손상",
        "keywords": {"손목", "삐었", "접질", "외상", "부딪", "통증", "붓기", "부종"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "손목 X-ray 5,000~20,000원 / 필요 시 초음파",
        "cost_treat": "부목·보조기·약물치료 10,000~80,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M65F": {
        "name": "손가락 건초염",
        "keywords": {"손가락", "손가락통증", "손가락아픔", "손마디", "붓기", "부종", "통증"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "손가락 X-ray 5,000~20,000원 / 초음파 30,000~80,000원",
        "cost_treat": "약물·보조기·주사치료 10,000~80,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "S63F": {
        "name": "손가락 염좌 또는 인대 손상",
        "keywords": {"손가락", "삐었", "접질", "외상", "부딪", "통증", "붓기", "부종"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "손가락 X-ray 5,000~20,000원 / 필요 시 초음파",
        "cost_treat": "부목·보조기·약물치료 10,000~80,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "S93": {
        "name": "발목 관절/인대 염좌",
        "keywords": {"발목", "삐었", "염좌", "인대", "접질렀"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "X-ray 5,000~15,000원 / 초음파 30,000~60,000원",
        "cost_treat": "부목/깁스 20,000~50,000원 / 물리치료 10,000~20,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M80": {
        "name": "골다공증",
        "keywords": {"골다공", "뼈", "골절", "골밀도"},
        "dept": "내과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "골밀도검사(DEXA) 20,000~40,000원",
        "cost_treat": "약물치료 월 30,000~80,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M05": {
        "name": "류마티스 관절염",
        "keywords": {"류마티스", "관절부음", "아침뻣뻣", "자가면역"},
        "dept": "류마티스내과", "category": "근골격계",
        "cost_clinic": "20,000~35,000원",
        "cost_test": "혈액검사(RF, CRP 등) 20,000~50,000원",
        "cost_treat": "생물학적제제 월 50만~100만원 (급여 시 본인부담 5~10%)",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M10": {
        "name": "통풍",
        "keywords": {"통풍", "엄지발가락", "요산", "발가락통증"},
        "dept": "류마티스내과", "category": "근골격계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "요산 혈액검사 5,000~15,000원",
        "cost_treat": "약물치료 월 10,000~30,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "M48": {
        "name": "척추관협착증",
        "keywords": {"척추", "협착", "다리저림", "보행장애", "척추관"},
        "dept": "정형외과", "category": "근골격계",
        "cost_clinic": "15,000~30,000원",
        "cost_test": "X-ray 5,000~15,000원 / MRI 150,000~350,000원",
        "cost_treat": "경막외 주사 50,000~150,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },

    # ── 소화기계 ──
    "K25": {
        "name": "위궤양",
        "keywords": {"위궤양", "속쓰림", "위통", "공복통", "소화불량"},
        "dept": "소화기내과", "category": "소화기계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "위내시경 50,000~100,000원 / HP 검사 10,000~30,000원",
        "cost_treat": "약물치료 월 20,000~50,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "K21": {
        "name": "역류성 식도염",
        "keywords": {"역류", "식도", "가슴쓰림", "속쓰림", "신트림"},
        "dept": "소화기내과", "category": "소화기계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "위내시경 50,000~100,000원",
        "cost_treat": "약물치료 월 15,000~40,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "K35": {
        "name": "급성 충수염 (맹장염)",
        "keywords": {"맹장", "충수", "우하복부", "복통", "구토"},
        "dept": "소화기내과", "category": "소화기계",
        "cost_clinic": "응급 진료 필요",
        "cost_test": "복부CT 100,000~200,000원 / 혈액검사 20,000~40,000원",
        "cost_treat": "수술 필수 (복강경)",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "K80": {
        "name": "담석증",
        "keywords": {"담석", "우상복부", "담낭", "명치"},
        "dept": "소화기내과", "category": "소화기계",
        "cost_clinic": "15,000~30,000원",
        "cost_test": "복부초음파 30,000~60,000원 / CT 100,000~200,000원",
        "cost_treat": "약물치료 또는 수술",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "C16": {
        "name": "위암",
        "keywords": {"위암", "위장", "체중감소", "혈변"},
        "dept": "소화기내과", "category": "소화기계",
        "cost_clinic": "20,000~35,000원",
        "cost_test": "위내시경 50,000~100,000원 / 조직검사 30,000~80,000원",
        "cost_treat": "항암치료 회당 50만~200만원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "C18": {
        "name": "대장암",
        "keywords": {"대장암", "혈변", "변비", "체중감소"},
        "dept": "소화기내과", "category": "소화기계",
        "cost_clinic": "20,000~35,000원",
        "cost_test": "대장내시경 80,000~150,000원 / 조직검사 30,000~80,000원",
        "cost_treat": "항암치료 회당 50만~200만원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "K57": {
        "name": "게실질환",
        "keywords": {"게실", "좌하복부", "발열", "설사"},
        "dept": "소화기내과", "category": "소화기계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "복부CT 100,000~200,000원 / 대장내시경 80,000~150,000원",
        "cost_treat": "항생제 치료 30,000~60,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },

    # ── 흉부·심장 ──
    "I20": {
        "name": "협심증",
        "keywords": {"가슴", "흉통", "압박", "심장", "방사통"},
        "dept": "심장내과", "category": "순환기계",
        "cost_clinic": "20,000~40,000원",
        "cost_test": "심전도 10,000~20,000원 / 흉부X-ray 5,000~15,000원 / 심장초음파 50,000~100,000원",
        "cost_treat": "약물치료 월 30,000~80,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "R07": {
        "name": "늑간신경통·흉막염",
        "keywords": {"옆구리", "찌르는", "숨쉴때", "늑간"},
        "dept": "호흡기내과", "category": "순환기계",
        "cost_clinic": "15,000~25,000원",
        "cost_test": "흉부X-ray 5,000~15,000원 / 흉부CT 100,000~200,000원",
        "cost_treat": "약물치료 10,000~30,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },

    # ── 이비인후 ──
    "J06": {
        "name": "급성 상기도 감염",
        "keywords": {"감기", "콧물", "기침", "인후통", "발열", "목아픔"},
        "dept": "이비인후과", "category": "호흡기계",
        "cost_clinic": "10,000~20,000원",
        "cost_test": "내시경·배양검사 필요 시 20,000~50,000원",
        "cost_treat": "약 처방 5,000~15,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "J32": {
        "name": "비염·부비동염",
        "keywords": {"코막힘", "콧물", "후비루", "비염", "축농증"},
        "dept": "이비인후과", "category": "호흡기계",
        "cost_clinic": "10,000~20,000원",
        "cost_test": "부비동X-ray 5,000~15,000원 / CT 80,000~150,000원",
        "cost_treat": "약물+비강세척 10,000~30,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },

    # ── 비뇨기 ──
    "N20": {
        "name": "요로결석",
        "keywords": {"옆구리", "혈뇨", "결석", "극심한통증"},
        "dept": "비뇨의학과", "category": "비뇨기계",
        "cost_clinic": "15,000~30,000원",
        "cost_test": "복부CT 100,000~200,000원 / 소변검사 5,000~10,000원",
        "cost_treat": "체외충격파쇄석 30만~60만원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
    "N30": {
        "name": "방광염·요로감염",
        "keywords": {"빈뇨", "배뇨통", "혈뇨", "방광"},
        "dept": "비뇨의학과", "category": "비뇨기계",
        "cost_clinic": "10,000~20,000원",
        "cost_test": "소변검사 5,000~10,000원 / 소변배양 10,000~20,000원",
        "cost_treat": "항생제 치료 10,000~30,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },

    # ── 피부 ──
    "L30": {
        "name": "접촉성 피부염·습진",
        "keywords": {"피부", "가려움", "발진", "두드러기", "습진"},
        "dept": "피부과", "category": "피부",
        "cost_clinic": "10,000~20,000원",
        "cost_test": "피부조직검사 30,000~60,000원 / 알레르기 혈액검사 30,000~80,000원",
        "cost_treat": "연고+약물 처방 10,000~30,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },

    # ── 신경 ──
    "G43": {
        "name": "편두통",
        "keywords": {"두통", "편두통", "박동성", "구역"},
        "dept": "신경과", "category": "신경계",
        "cost_clinic": "5,000~20,000원",
        "cost_test": "혈압 확인·신경학적 문진 5,000~20,000원 / 필요 시 혈액검사 10,000~50,000원",
        "cost_treat": "진통제·예방약 등 약물치료 5,000~60,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },

    # ── 안과 ──
    "H10": {
        "name": "결막염",
        "keywords": {"충혈", "눈", "분비물", "가려움"},
        "dept": "안과", "category": "안과",
        "cost_clinic": "10,000~20,000원",
        "cost_test": "안저검사 10,000~20,000원 / 시력검사 5,000~10,000원",
        "cost_treat": "점안약 5,000~15,000원",
        "cost_inpat": "200,000원~\n입원 날짜에 따라 달라짐.",
    },
}


# ═══════════════════════════════════════
#  부위 → 검사 항목 매핑 (프론트엔드 동기화)
# ═══════════════════════════════════════

AREA_TEST_MAP = {
    "배·소화기": "혈액검사·대변검사·복부초음파·필요 시 내시경/CT",
    "가슴·심장": "심전도·흉부 X-ray·혈액검사·심장효소검사",
    "무릎": "무릎 X-ray·초음파·필요 시 MRI",
    "어깨": "어깨 X-ray·초음파·필요 시 MRI",
    "등·허리": "척추 X-ray·신경학적 평가·필요 시 MRI",
    "손목·손가락": "손/손목 X-ray·초음파·신경전도검사·근전도",
    "팔·손": "팔/손 X-ray·초음파·신경전도검사·근전도",
    "발목·발가락": "발/발목 X-ray·초음파·필요 시 MRI",
    "다리·발": "하지 X-ray·초음파·신경전도검사·필요 시 MRI",
    "목·인후": "내시경·배양검사·혈액검사",
    "귀": "이경검사·청력검사·배양검사",
    "코": "비내시경·부비동 X-ray/CT",
    "눈": "시력검사·안압검사·세극등검사·안저검사·필요 시 OCT",
    "머리·두피": "혈압 확인·신경학적 문진·필요 시 혈액검사·두피 피부검사·위험 신호 시 CT/MRI",
    "피부": "피부확대경·진균검사·세균배양검사·필요 시 조직검사",
    "옆구리": "소변검사·혈액검사·복부초음파·필요 시 CT",
    "생식기·비뇨기": "소변검사·소변배양검사·초음파·성매개감염검사",
    "골반·사타구니": "소변검사·초음파·성매개감염검사",
    "항문·직장": "항문경·직장수지검사·대변검사·필요 시 대장내시경",
    "유방": "유방초음파·유방촬영·필요 시 조직검사",
}


# ═══════════════════════════════════════
#  진료 필요도 판정
# ═══════════════════════════════════════

EMERGENCY_WORDS = {"흉통", "호흡곤란", "의식", "마비", "대량출혈", "실신", "경련", "심정지"}
URGENT_WORDS = {"고열", "39도", "40도", "급성", "심한통증", "갑자기", "숨쉴때", "혈변", "혈뇨", "구토반복"}


def assess_urgency(
    symptom_text: str,
    areas: list[str],
    is_urgent_check: bool = False,
    duration: str = "",
) -> str:
    """진료 필요도 판정: 낮음/보통/높음"""
    text = symptom_text.lower()
    area_str = " ".join(areas)
    urgency = "낮음"

    # 긴급 키워드
    if is_urgent_check or any(w in text for w in EMERGENCY_WORDS):
        return "높음"

    if any(w in text for w in URGENT_WORDS):
        urgency = "보통"

    # 부위별 추가 상향
    if "가슴" in area_str and any(w in text for w in ["압박", "쥐어짜는", "방사통"]):
        return "높음"
    if ("가슴" in area_str or "옆구리" in area_str) and any(w in text for w in ["숨쉴때", "호흡", "찌르는"]):
        urgency = max(urgency, "보통", key=["낮음", "보통", "높음"].index)
    if "등·허리" in area_str and any(w in text for w in ["저림", "다리저림", "마비"]):
        urgency = max(urgency, "보통", key=["낮음", "보통", "높음"].index)
    if "머리" in area_str and any(w in text for w in ["갑자기", "최악", "구토", "의식"]):
        return "높음"

    # 증상 심각도 (여러 부위/증상)
    severity_words = ["악화", "점점", "계속", "반복", "매일", "못자", "일상생활", "지장"]
    if len(areas) >= 3 and urgency == "낮음":
        urgency = "보통"
    if any(w in text for w in severity_words) and urgency == "낮음":
        urgency = "보통"

    # 증상 기간
    dur = (duration or "").strip()
    if dur:
        import re
        date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", dur)
        if date_match:
            from datetime import datetime
            try:
                start = datetime.strptime(dur[:10], "%Y-%m-%d")
                diff_days = (datetime.now() - start).days
                if diff_days >= 30 and urgency == "낮음":
                    urgency = "보통"
                if diff_days >= 90:
                    urgency = max(urgency, "보통", key=["낮음", "보통", "높음"].index)
            except ValueError:
                pass
        for kw in ["몇달", "몇개월", "반년", "1년", "오래", "수년", "3개월"]:
            if kw in dur and urgency == "낮음":
                urgency = "보통"

    return urgency


# ═══════════════════════════════════════
#  증상 → KCD 코드 매핑
# ═══════════════════════════════════════

def symptom_to_kcd(user_input: str, areas: list[str] = None, top_n: int = 3) -> list[dict]:
    """부위 우선 + 증상 키워드 매칭"""
    if not user_input and not areas:
        return []

    tokens = set(re.split(r'[\s,\.。·]+', (user_input or "").strip()))
    tokens = {t for t in tokens if len(t) >= 2}

    # 부위에서 추가 키워드 추출
    area_str = " ".join(areas or [])
    area_tokens = set(re.split(r'[·\s]+', area_str))
    all_tokens = tokens | area_tokens

    explicit_text = (user_input or "").lower()
    explicit_wrist = "손목" in explicit_text or "손목터널" in explicit_text or "수근관" in explicit_text
    explicit_finger = "손가락" in explicit_text or "손끝" in explicit_text or "손마디" in explicit_text
    neuro_words = ["저림", "마비", "감각저하", "찌릿", "힘빠", "힘 빠"]
    neuro_sign = any(w in explicit_text for w in neuro_words)

    candidates = []
    for kcd_code, entry in KCD_MASTER.items():
        # 손목터널증후군은 '손목'이 명시되고 신경 증상이 있을 때만 후보로 올립니다.
        # 손가락 통증/붓기만 있는 경우에는 손가락 건초염·염좌·관절 문제를 우선합니다.
        if kcd_code == "G56" and not (explicit_wrist and neuro_sign):
            continue
        if kcd_code in {"M65F", "S63F"} and not explicit_finger:
            continue
        if kcd_code in {"M65W", "S63W"} and not explicit_wrist:
            continue
        keywords = entry["keywords"]
        overlap = all_tokens & keywords
        if overlap:
            score = round(len(overlap) / max(len(keywords), 1), 2)
            if kcd_code == "G56" and explicit_wrist and neuro_sign:
                score += 0.55
            if kcd_code in {"M65F", "S63F"} and explicit_finger:
                score += 0.35
            if kcd_code in {"M65W", "S63W"} and explicit_wrist:
                score += 0.35
            candidates.append({
                "kcd": kcd_code,
                "name": entry["name"],
                "confidence": score,
                "matched_keywords": list(overlap),
                "dept": entry["dept"],
                "category": entry["category"],
            })

    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return candidates[:top_n]


def get_kcd_info(kcd_code: str) -> Optional[dict]:
    if kcd_code in KCD_MASTER:
        return {"kcd": kcd_code, **KCD_MASTER[kcd_code]}
    kcd3 = kcd_code[:3]
    if kcd3 in KCD_MASTER:
        return {"kcd": kcd3, **KCD_MASTER[kcd3]}
    return None


def get_cost_by_kcd(kcd_code: str) -> Optional[dict]:
    info = get_kcd_info(kcd_code)
    if not info:
        return None
    return {
        "kcd": info["kcd"],
        "name": info["name"],
        "category": info.get("category", ""),
        "dept": info.get("dept", ""),
        "cost_clinic": info.get("cost_clinic", ""),
        "cost_test": info.get("cost_test", ""),
        "cost_treat": info.get("cost_treat", ""),
        "cost_inpat": info.get("cost_inpat", ""),
    }


def get_test_for_area_and_symptom(areas: list[str], symptom_text: str = "") -> str:
    """부위와 증상 키워드를 함께 고려한 검사 항목 반환."""
    text = f"{symptom_text or ''} {' '.join(areas or [])}".lower()
    has = lambda words: any(w in text for w in words)
    area_set = set(areas or [])
    limb = bool(area_set & {"팔·손", "손목·손가락", "다리·발", "발목·발가락", "무릎", "어깨"})

    if has(["의식", "말 어눌", "한쪽 마비", "편측", "경련", "극심한 두통", "벼락두통"]):
        return "뇌 CT·MRI·혈액검사·신경학적 평가"
    if has(["가려움", "발진", "홍반", "분비물", "진물", "고름", "무좀", "습진", "피부염"]) and (limb or "피부" in area_set or "머리·두피" in area_set):
        extra = "·신경전도검사·근전도" if has(["저림", "마비", "감각저하"]) else ""
        return f"피부확대경·진균검사·세균배양검사·염증검사{extra}"
    if ("손목·손가락" in area_set or "팔·손" in area_set) and has(["저림", "마비", "감각저하", "찌릿"]):
        if "손가락" in text and "손목" not in text:
            return "손가락 X-ray·초음파·필요 시 신경전도검사"
        return "신경전도검사·근전도·손목 X-ray·초음파"
    if ("다리·발" in area_set or "발목·발가락" in area_set) and has(["저림", "마비", "감각저하", "찌릿"]):
        return "신경전도검사·근전도·하지 X-ray·필요 시 MRI"
    if limb and has(["접질", "삐", "외상", "부종", "붓", "통증", "골절", "염좌"]):
        return "해당 부위 X-ray·초음파·필요 시 MRI"

    return get_test_for_area(areas)


def get_test_for_area(areas: list[str]) -> str:
    """부위에 맞는 기본 검사 항목 반환"""
    for area in (areas or []):
        if area in AREA_TEST_MAP:
            return AREA_TEST_MAP[area]
    return "혈액검사·소변검사 등"
