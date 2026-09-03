from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings

REPO = Path(__file__).resolve().parents[2]
LOCALHOST_URL = "postgresql+psycopg://finance:finance@localhost:5432/finance"
RDS_HOST = "finance.cluster-xyz.eu-west-2.rds.amazonaws.com"


@pytest.mark.unit
def test_alembic_targets_effective_postgres_url_instead_of_localhost():
    env_source = (REPO / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "settings.effective_database_url" in env_source
    assert 'set_main_option("sqlalchemy.url", settings.database_url)' not in env_source

    settings = Settings(
        _env_file=None,
        app_env="aws",
        database_url=LOCALHOST_URL,
        postgres_host=RDS_HOST,
        postgres_user="finance",
        postgres_password="s3cret",
        postgres_db="finance",
        postgres_port=5432,
    )
    url = settings.effective_database_url

    assert settings.database_url == LOCALHOST_URL
    assert url != settings.database_url
    assert "localhost" not in url
    assert RDS_HOST in url
    assert url == f"postgresql+psycopg://finance:s3cret@{RDS_HOST}:5432/finance"
