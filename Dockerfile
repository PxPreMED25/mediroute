# ═══════════════════════════════════════
#  MediRoute 백엔드 — Production Dockerfile
# ═══════════════════════════════════════

FROM python:3.12-slim AS base

# 시스템 패키지 (PostgreSQL 클라이언트 등)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 설치 (레이어 캐시 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . .

# .env 파일은 복사하지 않음 (환경변수로 주입)
RUN rm -f .env .env.example

# 비root 사용자로 실행
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

# 포트 (Railway는 PORT 환경변수 자동 주입)
EXPOSE 8000

# 헬스체크
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 실행 (PORT 환경변수 자동 대응)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
