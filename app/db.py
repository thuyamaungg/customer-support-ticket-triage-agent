"""Database engine, session factory and declarative base."""
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    """Create an engine. SQLite gets extra settings so concurrent writers queue up safely."""
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)

    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        # Let SQLAlchemy (not the sqlite3 driver) decide when transactions begin.
        dbapi_conn.isolation_level = None
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        # BEGIN IMMEDIATE takes the write lock up front. Without it, two requests that
        # both read first and then write can collide with "database is locked".
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(bind: Engine = engine) -> None:
    """Create all tables (fine for development; use migrations in production)."""
    from app import models  # noqa: F401  (import registers the tables)

    Base.metadata.create_all(bind)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with SessionLocal() as session:
        yield session


def get_session_factory() -> sessionmaker:
    """FastAPI dependency for background jobs, which need their own session after the
    request's session has closed. Overridden in tests."""
    return SessionLocal
