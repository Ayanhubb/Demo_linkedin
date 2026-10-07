from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _read_dotenv_database_url() -> str | None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("DATABASE_URL="):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _normalize_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def _ensure_database(admin_url: str, database_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database_name},
            ).scalar()
            if exists:
                return
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    source = os.environ.get("DATABASE_URL") or _read_dotenv_database_url()
    if not source:
        pytest.exit("DATABASE_URL is not set. Copy .env.example to .env and start Postgres.")

    dev_url = make_url(_normalize_database_url(source))
    test_url = dev_url.set(database="linkedin_scheduler_test")
    os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    from app.core.config import reset_settings
    from app.db.database import reset_database_state

    reset_settings()
    reset_database_state()
    _ensure_database(dev_url.render_as_string(hide_password=False), "linkedin_scheduler_test")

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    yield
    reset_database_state()
    reset_settings()


@pytest.fixture
def db_session(migrated_database: None) -> Iterator[Session]:
    del migrated_database
    from app.db.database import get_engine

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        name = item.path.name
        if name == "test_tokens.py" or name == "test_logging.py":
            item.add_marker(pytest.mark.unit)
        elif name == "test_database.py":
            item.add_marker(pytest.mark.database)
        elif name == "test_worker.py":
            item.add_marker(pytest.mark.celery)
        elif name == "test_linkedin_posts.py":
            item.add_marker(pytest.mark.linkedin)
        elif name == "test_linkedin_oauth.py":
            item.add_marker(pytest.mark.linkedin)
            item.add_marker(pytest.mark.api)
        elif name in {"test_schedule_api.py", "test_dashboard.py", "test_health.py", "test_api_errors.py"}:
            item.add_marker(pytest.mark.api)
