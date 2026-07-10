from erp.core.settings import Settings


def test_env_overrides_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ERP_ENV", "test")
    monkeypatch.setenv("ERP_JWT_ACCESS_TTL_SECONDS", "60")
    s = Settings()
    assert s.env == "test"
    assert s.jwt_access_ttl_seconds == 60


def test_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.env == "dev"
    assert s.database_url.startswith("postgresql+psycopg://")
