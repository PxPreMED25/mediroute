"""
데이터베이스 설정 (STEP 4)
- 개발: SQLite (aiosqlite)
- 운영: PostgreSQL (asyncpg) — .env에서 DATABASE_URL만 변경
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def get_database_url() -> str:
    settings = get_settings()
    url = settings.DATABASE_URL
    # SQLAlchemy async 드라이버 변환
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://")
    return url


engine = create_async_engine(
    get_database_url(),
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """FastAPI Depends용 DB 세션 생성기"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """테이블 생성 (앱 시작 시 호출)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """엔진 종료"""
    await engine.dispose()
