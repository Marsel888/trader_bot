from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import event, text
from src.config import cfg
from src.database.models import Base
from loguru import logger


engine = create_async_engine(
    cfg.DATABASE_URL,
    echo=False,
    connect_args={"timeout": 30},  # wait up to 30s for lock
)

# Enable WAL mode — allows concurrent reads + one writer without "database is locked"
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(conn, _):
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrations for columns added after initial release
        for migration in [
            "ALTER TABLE trades ADD COLUMN current_price REAL",
            "ALTER TABLE trades ADD COLUMN leverage INTEGER DEFAULT 5",
        ]:
            try:
                await conn.execute(text(migration))
            except Exception:
                pass  # column already exists
    logger.info("Database initialized")


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
