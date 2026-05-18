from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
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
            "application_type": "VARCHAR(100) NOT NULL DEFAULT 'unknown'",
            "normalized_url": "VARCHAR(1000) NOT NULL DEFAULT ''",
            "url_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "source_id": "INTEGER",
            "telegram_notified": "BOOLEAN NOT NULL DEFAULT 0",
            "telegram_notified_at": "DATETIME",
        },
        "job_search_sources": {
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "failure_count": "INTEGER NOT NULL DEFAULT 0",
            "paused_until": "DATETIME",
            "last_failed_at": "DATETIME",
        },
        "discarded_jobs": {
            "portal": "VARCHAR(100) NOT NULL DEFAULT ''",
            "source_id": "INTEGER",
            "target_role": "VARCHAR(100) NOT NULL DEFAULT ''",
            "title": "VARCHAR(255) NOT NULL DEFAULT ''",
            "company": "VARCHAR(255) NOT NULL DEFAULT ''",
            "location": "VARCHAR(255) NOT NULL DEFAULT ''",
            "modality": "VARCHAR(100) NOT NULL DEFAULT ''",
            "salary": "VARCHAR(100) NOT NULL DEFAULT ''",
            "url": "VARCHAR(1000) NOT NULL DEFAULT ''",
            "description": "TEXT NOT NULL DEFAULT ''",
            "requirements": "TEXT NOT NULL DEFAULT ''",
            "raw_posted_text": "TEXT NOT NULL DEFAULT ''",
            "application_type": "VARCHAR(100) NOT NULL DEFAULT 'unknown'",
            "compatibility_score": "FLOAT",
            "discard_reasons": "TEXT NOT NULL DEFAULT '[]'",
            "detected_keywords": "TEXT NOT NULL DEFAULT '[]'",
            "source_url": "VARCHAR(2000) NOT NULL DEFAULT ''",
            "found_at": "DATETIME",
            "created_at": "DATETIME",
            "normalized_url": "VARCHAR(1000) NOT NULL DEFAULT ''",
            "url_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "seen_count": "INTEGER NOT NULL DEFAULT 1",
            "last_seen_at": "DATETIME",
        },
    }

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, columns in required_columns.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
            for column_name, column_ddl in columns.items():
                if column_name in existing_columns:
                    continue
                try:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}"))
                except OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
