from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def create_sqlite_engine(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", future=True)


def create_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db(engine) -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    _apply_sqlite_migrations(engine)


def test_connection(engine) -> None:
    with Session(engine) as session:
        session.execute(text("SELECT 1"))


def _apply_sqlite_migrations(engine) -> None:
    required_columns = {
        "job_offers": {
            "published_at": "DATETIME",
            "found_at": "DATETIME",
            "raw_posted_text": "TEXT NOT NULL DEFAULT ''",
            "normalized_url": "VARCHAR(1000) NOT NULL DEFAULT ''",
            "url_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "source_id": "INTEGER",
        },
        "job_search_sources": {
            "last_error": "TEXT NOT NULL DEFAULT ''",
        },
    }

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, columns in required_columns.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_ddl in columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}"))
