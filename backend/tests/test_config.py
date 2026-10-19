import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_database_url_uses_psycopg_and_escapes_password() -> None:
    settings = make_settings(postgres_password="p@ss/word", postgres_host="db", postgres_port=6543)

    url = settings.database_url

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "db"
    assert url.port == 6543
    assert url.password == "p@ss/word"
    assert "p@ss/word" not in repr(url)


def test_password_is_hidden_in_repr() -> None:
    settings = make_settings(postgres_password="s3cret")

    assert "s3cret" not in repr(settings)


def test_placeholder_password_allowed_in_dev() -> None:
    assert make_settings(app_env="dev", postgres_password="change-me").app_env == "dev"


@pytest.mark.parametrize("password", ["change-me", ""])
def test_placeholder_password_rejected_in_prod(password: str) -> None:
    with pytest.raises(ValidationError, match="POSTGRES_PASSWORD"):
        make_settings(app_env="prod", postgres_password=password)


def test_unknown_app_env_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(app_env="staging")
