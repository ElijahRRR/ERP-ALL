"""启动自检门禁（RS-02a / D-Q68）：已知默认密钥必须让进程起不来。

这些用例**故意把 `tests/conftest.py` 的全局放行开关关掉**（init kwarg 优先级高于
环境变量），否则「全局放行」会让被测的门禁在测试里永远不触发——即门禁自己没人验。
"""

import pytest
from pydantic import ValidationError

from erp.core.settings import Settings

_STRONG = "9f2c1ab47d3e5068bb90cf1e2a4d7c3f9f2c1ab47d3e5068bb90cf1e2a4d7c3f"

_SAFE_KWARGS = {
    "database_url": f"postgresql+psycopg://erp_app:{_STRONG}@db:5432/erp_all",
    "migrator_database_url": f"postgresql+psycopg://erp_migrator:{_STRONG}@db:5432/erp_all",
    "redis_url": f"redis://:{_STRONG}@redis:6379/0",
    "jwt_secret": _STRONG,
    "credential_key": _STRONG,
}


@pytest.fixture(autouse=True)
def _no_erp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉进程里所有 ERP_* 变量：CI 给的是弱口令 DSN，会污染判据。"""
    import os

    for name in [k for k in os.environ if k.startswith("ERP_")]:
        monkeypatch.delenv(name, raising=False)


def _build(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"allow_insecure_defaults": False, **_SAFE_KWARGS, **overrides}
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_strong_values_pass() -> None:
    s = _build()
    assert s.insecure_findings() == []


def test_repo_shipped_defaults_are_refused() -> None:
    """**反向不变量**：仓里带的那套默认值必须被拒。

    这条不是重复 test_field_* ——它盯的是「有人把默认值换成另一个同样人人皆知的串、
    而 `_INSECURE_SECRETS` 没跟着加」的情形。判据锚在**实际默认值**上，
    改默认值而不更新弱值表，这里就会红。
    """
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, allow_insecure_defaults=False)  # type: ignore[call-arg]
    msg = str(e.value)
    for expected in (
        "ERP_JWT_SECRET",
        "ERP_CREDENTIAL_KEY",
        "ERP_DATABASE_URL",
        "ERP_MIGRATOR_DATABASE_URL",
        "ERP_REDIS_URL",
    ):
        assert expected in msg, f"{expected} 没被自检认出来：{msg}"


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("jwt_secret", "dev-only-change-me-padding-to-32-bytes!", "ERP_JWT_SECRET"),
        ("credential_key", "dev-only-change-me", "ERP_CREDENTIAL_KEY"),
        ("credential_key", "CHANGE-ME", "ERP_CREDENTIAL_KEY"),  # 大小写不敏感
        (
            "database_url",
            "postgresql+psycopg://erp_app:erp_app@db:5432/erp_all",
            "ERP_DATABASE_URL",
        ),
        (
            "migrator_database_url",
            "postgresql+psycopg://erp_migrator:erp_migrator@db:5432/erp_all",
            "ERP_MIGRATOR_DATABASE_URL",
        ),
        (
            "database_url",  # 口令为空同样算「没设」
            "postgresql+psycopg://erp_app@db:5432/erp_all",
            "ERP_DATABASE_URL",
        ),
        ("redis_url", "redis://redis:6379/0", "ERP_REDIS_URL"),  # 无 requirepass
    ],
)
def test_each_weak_value_is_caught(field: str, value: str, needle: str) -> None:
    with pytest.raises(ValidationError) as e:
        _build(**{field: value})
    assert needle in str(e.value)


def test_allow_flag_permits_outside_prod() -> None:
    s = Settings(_env_file=None, allow_insecure_defaults=True, env="dev")  # type: ignore[call-arg]
    assert s.insecure_findings(), "夹具前提没了：默认值应当仍是弱值"


def test_allow_flag_is_void_in_prod() -> None:
    """prod 下放行开关无效——否则「加个环境变量就能重新开门」，门禁等于没有。"""
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, allow_insecure_defaults=True, env="prod")  # type: ignore[call-arg]
    assert "prod" in str(e.value)


def test_findings_are_specific_not_boolean() -> None:
    """自检要说清是哪一项——只回 True/False 的话，部署机拿不到可执行的下一步。"""
    s = Settings(_env_file=None, allow_insecure_defaults=True)  # type: ignore[call-arg]
    found = s.insecure_findings()
    assert len(found) == 5
    assert all(f.startswith("ERP_") for f in found)
