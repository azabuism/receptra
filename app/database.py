"""Database configuration"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# Database engine will be created based on DATABASE_URL from config
engine = None
AsyncSessionLocal = None


def init_db(database_url: str):
    """Initialize database connection"""
    global engine, AsyncSessionLocal
    engine = create_async_engine(
        database_url,
        echo=False,
        future=True,
    )
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False
    )


async def get_db():
    """Dependency for database session"""
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized")
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
