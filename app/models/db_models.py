"""
DB 모델 정의 (STEP 4)
- User: 사용자 (소셜 로그인)
- FamilyMember: 가족 구성원 프로필
- SymptomHistory: 증상 분석 이력
- FavoriteHospital: 즐겨찾기 병원
"""

import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float,
    DateTime, ForeignKey, JSON,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 소셜 로그인 정보
    provider = Column(String(20), nullable=False)  # kakao / naver / google
    provider_id = Column(String(100), nullable=False, unique=True)
    email = Column(String(200), default="")
    nickname = Column(String(100), default="")
    profile_image = Column(String(500), default="")

    # 앱 내 정보
    default_region = Column(String(100), default="")  # 기본 지역

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # 관계
    family_members = relationship("FamilyMember", back_populates="user", cascade="all, delete-orphan")
    symptom_histories = relationship("SymptomHistory", back_populates="user", cascade="all, delete-orphan")
    favorite_hospitals = relationship("FavoriteHospital", back_populates="user", cascade="all, delete-orphan")


class FamilyMember(Base):
    """가족 구성원 (소아 증상 입력 시 자녀 정보 자동 반영)"""
    __tablename__ = "family_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(50), nullable=False)
    relation = Column(String(20), default="")  # 본인/자녀/배우자/부모
    birth_year = Column(Integer, default=0)
    gender = Column(String(10), default="")
    disease = Column(String(200), default="")  # 기저질환
    meds = Column(String(500), default="")  # 복용 중 약물

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="family_members")


class SymptomHistory(Base):
    """증상 분석 이력 (지난 검색 기록 + 결과)"""
    __tablename__ = "symptom_histories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # 입력값 저장
    symptom = Column(Text, default="")
    areas = Column(JSON, default=list)
    age = Column(String(10), default="")
    gender = Column(String(10), default="")
    region = Column(String(100), default="")
    duration = Column(String(50), default="")
    meds = Column(String(500), default="")
    disease = Column(String(200), default="")
    is_urgent = Column(Boolean, default=False)

    # 결과 요약 저장
    predicted_diseases = Column(JSON, default=list)   # [{"name": ..., "reason": ...}]
    recommended_depts = Column(JSON, default=list)     # ["정형외과", "내과"]
    urgency_text = Column(String(20), default="낮음")
    kcd_code = Column(String(10), default="")          # 매칭된 KCD 코드

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="symptom_histories")


class FavoriteHospital(Base):
    """즐겨찾기 병원"""
    __tablename__ = "favorite_hospitals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    hospital_name = Column(String(200), nullable=False)
    hospital_address = Column(String(300), default="")
    hospital_type = Column(String(50), default="")  # 의원/전문병원/대학병원
    dept = Column(String(50), default="")
    ykiho = Column(String(200), default="")  # 심평원 암호화 요양기호
    lat = Column(Float, default=0.0)
    lng = Column(Float, default=0.0)
    memo = Column(Text, default="")  # 사용자 메모

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="favorite_hospitals")
