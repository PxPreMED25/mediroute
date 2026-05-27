"""
증상 분석 API 라우터
POST /api/analyze — 프론트엔드의 callClaude()를 대체
"""

import logging
import time
from fastapi import APIRouter, HTTPException
from app.models.schemas import AnalyzeRequest, AnalyzeResponse, ErrorResponse
from app.services.claude_service import analyze_symptoms

logger = logging.getLogger("mediroute.router.analyze")

router = APIRouter(prefix="/api", tags=["증상 분석"])


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Claude API 오류"},
        422: {"model": ErrorResponse, "description": "입력 데이터 오류"},
    },
    summary="증상 분석 및 진료과 추천",
    description="""
    사용자가 입력한 증상, 부위, 나이, 성별, 지역 등을 분석하여
    예상 질환, 추천 진료과, 주변 병원, 예상 비용을 반환합니다.
    
    현재는 Claude API를 통해 분석하며,
    향후 실제 병원 DB + 진료비 통계 데이터로 보강됩니다.
    """,
)
async def analyze(req: AnalyzeRequest):
    start = time.time()

    # 기본 입력 검증
    if not req.symptom and not req.areas:
        raise HTTPException(
            status_code=422,
            detail="증상 텍스트 또는 부위를 하나 이상 입력해주세요.",
        )

    try:
        result = await analyze_symptoms(req, user_lat=req.lat, user_lng=req.lng)

        elapsed = round(time.time() - start, 2)
        logger.info(
            f"분석 완료 ({elapsed}s) | "
            f"symptom={req.symptom[:20]}... | "
            f"region={req.region} | "
            f"urgent={result.isUrgent}"
        )

        return result

    except ValueError as e:
        logger.error(f"분석 실패 (ValueError): {e}")
        raise HTTPException(status_code=500, detail=str(e))

    except RuntimeError as e:
        logger.error(f"분석 실패 (RuntimeError): {e}")
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        logger.error(f"분석 실패 (예상치 못한 오류): {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="증상 분석 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        )
