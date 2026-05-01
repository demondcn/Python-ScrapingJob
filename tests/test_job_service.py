from pathlib import Path

from sqlalchemy.orm import Session

from src.jobops_assistant.database import create_session_factory, create_sqlite_engine, init_db
from src.jobops_assistant.job_service import create_offer, list_offers


def test_offer_deduplication(tmp_path: Path):
    engine = create_sqlite_engine(tmp_path / "test.db")
    init_db(engine)
    session_factory = create_session_factory(engine)

    with Session(session_factory.kw["bind"]) as session:
        first = create_offer(
            session,
            title="Backend Junior",
            company="Acme",
            portal="Portal",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/offer",
            description="Python y SQL",
            requirements="Junior",
        )
        second = create_offer(
            session,
            title="Backend Junior",
            company="Acme",
            portal="Portal",
            location="Bogota",
            modality="Remoto",
            salary="",
            url="https://example.com/offer",
            description="Python y SQL",
            requirements="Junior",
        )
        offers = list_offers(session)
        assert first.id == second.id
        assert len(offers) == 1

