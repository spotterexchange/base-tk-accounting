from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

# pool_pre_ping: Render Postgres closes idle connections; without the ping a
# stale pooled connection surfaces as a 500 on the first request after a
# quiet period. pool_recycle proactively retires connections before that.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
