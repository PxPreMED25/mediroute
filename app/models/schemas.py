"""
MediRoute API 스키마 정의
프론트엔드 ↔ 백엔드 간 데이터 구조
"""

from pydantic import BaseModel, Field
from typing import Optional


# ═══════════════════════════════════════
#  증상 분석 요청 (프론트 → 백엔드)
# ═══════════════════════════════════════

class AnalyzeRequest(BaseModel):
    """프론트엔드의 state 객체와 1:1 매핑"""
    symptom: str = Field(default="", description="증상 텍스트")
    areas: list[str] = Field(default_factory=list, description="선택된 부위 목록")
    age: str = Field(default="", description="나이")
    gender: str = Field(default="", description="성별")
    region: str = Field(default="", description="지역 (시도 시군구 읍면동)")
    duration: str = Field(default="", description="증상 시작 시기")
    meds: str = Field(default="", description="복용 중인 약")
    disease: str = Field(default="", description="기저질환")
    checks: list[str] = Field(default_factory=list, description="응급 체크 항목")
    is_urgent: bool = Field(default=False, description="응급 여부")
    # STEP 2: GPS 좌표 (프론트에서 navigator.geolocation으로 전달)
    lat: Optional[float] = Field(default=None, description="사용자 위도 (GPS)")
    lng: Optional[float] = Field(default=None, description="사용자 경도 (GPS)")


# ═══════════════════════════════════════
#  증상 분석 응답 (백엔드 → 프론트)
#  현재 프론트의 applyResult(r)이 기대하는 구조 그대로
# ═══════════════════════════════════════

class DeptInfo(BaseModel):
    name: str
    reason: str


class DiseaseInfo(BaseModel):
    name: str
    reason: str


class HospitalInfo(BaseModel):
    name: str
    type: str  # 의원 / 전문병원 / 대학병원
    dept: str
    address: str = ""
    hours: str = ""
    fit: str = ""
    distanceKm: float = 0.0


class AnalyzeResponse(BaseModel):
    """프론트엔드의 applyResult()가 기대하는 JSON 구조"""
    isUrgent: bool = False
    areaText: str = ""
    symptomText: str = ""
    urgencyText: str = "낮음"  # 낮음 / 보통 / 높음
    predictedDiseases: list[DiseaseInfo] = []
    dept1: Optional[DeptInfo] = None
    dept2: Optional[DeptInfo] = None
    dept3: Optional[DeptInfo] = None
    hospGuide: str = ""
    memoSay: list[str] = []
    memoAsk: list[str] = []
    routeBest: str = ""
    routeFast: str = ""
    routeCheap: str = ""
    routePro: str = ""
    costClinic: str = ""
    costTest: str = ""
    costTreat: str = ""
    costInpat: str = ""
    nearbyHospitals: list[HospitalInfo] = []


# ═══════════════════════════════════════
#  에러 응답
# ═══════════════════════════════════════

class ErrorResponse(BaseModel):
    error: str
    detail: str = ""


# ═══════════════════════════════════════
#  헬스체크
# ═══════════════════════════════════════

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = ""
