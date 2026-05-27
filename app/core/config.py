"""
MediRoute 백엔드 설정
환경변수를 .env 파일에서 읽어옴
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── 앱 기본 ──
    APP_NAME: str = "MediRoute API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ── Claude API ──
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    CLAUDE_MAX_TOKENS: int = 1500

    # ── CORS (프론트엔드 URL) ──
    FRONTEND_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "https://pxpremed25.github.io",
        "https://www.pxpremed25.github.io",
    ]

    @property
    def all_origins(self) -> list[str]:
        """FRONTEND_URL이 ALLOWED_ORIGINS에 없으면 자동 추가"""
        origins = list(self.ALLOWED_ORIGINS)
        if self.FRONTEND_URL and self.FRONTEND_URL not in origins:
            origins.append(self.FRONTEND_URL)
        return origins

    # ── Rate Limiting ──
    RATE_LIMIT_PER_MINUTE: int = 20

    # ── 공공데이터 API (STEP 2에서 사용) ──
    DATA_GO_KR_API_KEY: str = ""

    # ── 카카오맵 API (STEP 2에서 사용) ──
    KAKAO_REST_API_KEY: str = ""

    # ── 네이버 지도/지역 검색 API (GPS 기반 실제 병원 검색) ──
    # NAVER_SEARCH_CLIENT_ID/SECRET: 네이버 개발자센터 지역검색 API
    # NAVER_MAPS_CLIENT_ID/SECRET: 네이버 클라우드 지도 Geocoding/Directions API
    NAVER_SEARCH_CLIENT_ID: str = ""
    NAVER_SEARCH_CLIENT_SECRET: str = ""
    NAVER_MAPS_CLIENT_ID: str = ""
    NAVER_MAPS_CLIENT_SECRET: str = ""

    # ── DB (STEP 4) ──
    DATABASE_URL: str = "sqlite:///./mediroute.db"

    # ── JWT 인증 (STEP 4) ──
    JWT_SECRET_KEY: str = "mediroute-dev-secret-change-in-production"

    # ── 카카오/네이버 OAuth (STEP 4) ──
    KAKAO_CLIENT_ID: str = ""
    NAVER_CLIENT_ID: str = ""
    NAVER_CLIENT_SECRET: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
