"""
MediRoute 백엔드 — FastAPI 앱 진입점

실행 방법:
  uvicorn app.main:app --reload --port 8000

API 문서:
  http://localhost:8000/docs  (Swagger UI)
  http://localhost:8000/redoc (ReDoc)
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import get_settings
from app.routers import analyze, hospitals, cost, auth, userdata
from app.core.database import init_db, close_db

# ═══════════════════════════════════════
#  로깅 설정
# ═══════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mediroute")


# ═══════════════════════════════════════
#  앱 생명주기
# ═══════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(f"🚀 {settings.APP_NAME} {settings.APP_VERSION} 시작")

    # API 키 존재 여부 확인
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("⚠️  ANTHROPIC_API_KEY가 설정되지 않았습니다!")
    else:
        logger.info("✅ ANTHROPIC_API_KEY 확인됨")

    if settings.DATA_GO_KR_API_KEY:
        logger.info("✅ DATA_GO_KR_API_KEY 확인됨 (STEP 2 준비 완료)")
    if settings.KAKAO_REST_API_KEY:
        logger.info("✅ KAKAO_REST_API_KEY 확인됨 (STEP 2 준비 완료)")

    # STEP 4: DB 초기화
    await init_db()
    logger.info("✅ 데이터베이스 초기화 완료")

    yield

    await close_db()
    logger.info(f"👋 {settings.APP_NAME} 종료")


# ═══════════════════════════════════════
#  FastAPI 앱 생성
# ═══════════════════════════════════════

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
## MediRoute 백엔드 API

증상 기반 진료과 추천 및 주변 의료기관 안내 서비스

### 현재 기능
- `POST /api/analyze` — 증상 분석 + 진료과 추천 + 실제 병원 + 실제 비용 (STEP 1~3)
- `GET /api/hospitals` — GPS 기반 실제 병원 검색 (STEP 2)
- `GET /api/kcd` — 증상 텍스트 → KCD 코드 매핑 (STEP 3)
- `GET /api/cost` — KCD 기반 진료비 조회 (STEP 3)
- `GET /api/kcd-list` — 지원 KCD 목록 조회

### 예정 기능
- 사용자 인증 + 이력 저장 (STEP 4)
- 보험사 연동, 이미지 분석 (STEP 5+)
    """,
    lifespan=lifespan,
)


# ═══════════════════════════════════════
#  CORS 미들웨어
# ═══════════════════════════════════════

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.all_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════
#  라우터 등록
# ═══════════════════════════════════════

app.include_router(analyze.router)
app.include_router(hospitals.router)
app.include_router(cost.router)
app.include_router(auth.router)
app.include_router(userdata.router)


# ═══════════════════════════════════════
#  헬스체크 & 루트
# ═══════════════════════════════════════

@app.get("/", tags=["기본"])
async def root():
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }


@app.get("/health", tags=["기본"])
async def health():
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "claude_api": bool(settings.ANTHROPIC_API_KEY),
        "data_go_kr_api": bool(settings.DATA_GO_KR_API_KEY),
        "kakao_api": bool(settings.KAKAO_REST_API_KEY),
    }
