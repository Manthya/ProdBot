from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from chatbot_ai_system.config import get_settings

settings = get_settings()

# Use asyncpg driver
if getattr(settings, "database_url", None):
    DATABASE_URL = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
else:
    raise RuntimeError("DATABASE_URL is not set in settings.")

engine = create_async_engine(DATABASE_URL, echo=settings.debug)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
