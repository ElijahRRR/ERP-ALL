"""启动自检门禁（RS-02a / D-Q68）：已知默认密钥必须让进程起不来。

这些用例**故意把 `tests/conftest.py` 的全局放行开关关掉**（init kwarg 优先级高于
环境变量），否则「全局放行」会让被测的门禁在测试里永远不触发——即门禁自己没人验。
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from erp.core.settings import Settings, get_settings

_STRONG = "9f2c1ab47d3e5068bb90cf1e2a4d7c3f9f2c1ab47d3e5068bb90cf1e2a4d7c3f"

_SAFE_KWARGS = {
    "database_url": f"postgresql+psycopg://erp_app:{_STRONG}@db:5432/erp_all",
    "migrator_database_url": f"postgresql+psycopg://erp_migrator:{_STRONG}@db:5432/erp_all",
    "redis_url": f"redis://:{_STRONG}@redis:6379/0",
    "jwt_secret": _STRONG,
    "credential_key": _STRONG,
}


@pytest.fixture(autouse=True)
def _no_erp_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """清掉进程里所有 ERP_* 变量：CI 给的是弱口令 DSN，会污染判据。"""
    import os

    for name in [k for k in os.environ if k.startswith("ERP_")]:
        monkeypatch.delenv(name, raising=False)
    # 本文件里有用例经 get_settings() 走真实入口，别把结果留给别人
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
        # 〔审查 AI 的 S3〕这两个此前判成安全——同样的值在库 DSN 里却会被抓
        ("redis_url", "redis://:@redis:6379/0", "ERP_REDIS_URL"),  # 空口令
        ("redis_url", "redis://:changeme@redis:6379/0", "ERP_REDIS_URL"),  # 弱口令
    ],
)
def test_each_weak_value_is_caught(field: str, value: str, needle: str) -> None:
    with pytest.raises(ValidationError) as e:
        _build(**{field: value})
    assert needle in str(e.value)


@pytest.mark.parametrize("env", ["dev", "test"])
def test_allow_flag_permits_only_whitelisted_envs(env: str) -> None:
    s = Settings(_env_file=None, allow_insecure_defaults=True, env=env)  # type: ignore[call-arg]
    assert s.insecure_findings(), "夹具前提没了：默认值应当仍是弱值"


@pytest.mark.parametrize("env", ["prod", "production", "Prod", "PROD", "staging", ""])
def test_allow_flag_is_void_outside_whitelist(env: str) -> None:
    """白名单之外一律拒绝放行——**包括拼写变体**。

    〔2026-07-27 审查 AI 的 S1〕判据原写 `self.env != "prod"`，是精确、大小写敏感的
    黑名单：`production` / `Prod` / `PROD` 任一写法都让保护完全失效，而 `env: str`
    没有任何取值约束。改白名单后未知取值一律 fail-closed。
    """
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, allow_insecure_defaults=True, env=env)  # type: ignore[call-arg]
    assert "必须换成真实密钥" in str(e.value)


def test_refusal_hint_does_not_teach_bypass_outside_whitelist() -> None:
    """白名单之外的报错**不许**提「设个开关就能放行」。

    〔同 S1〕原提示语写「ERP_ENV=prod 下无效」，而部署机当时 env 恰好是 dev——
    半夜起不来的人读到那句，合理推论就是「我这台不是 prod，那我能用」。
    报错出现的场合恰恰是唯一绝不该走这条路的机器上，措辞不能反过来教人绕过。
    """
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, allow_insecure_defaults=False, env="prod")  # type: ignore[call-arg]
    msg = str(e.value)
    assert "必须换成真实密钥" in msg
    assert "=1 可放行" not in msg, "白名单之外的报错不该给出放行姿势"


def test_env_has_no_consumers_outside_settings() -> None:
    """`Settings.env` 只许用于弱密钥放行的环境判定，不许再承载别的开关。

    〔2026-07-27 审查 AI 的 N1〕`main.py` 曾用 `settings.env != "prod"` 决定要不要挂
    Swagger UI。S1 把部署机的 `env` 从 `"dev"` 翻成 `"prod"` 之后接口文档随之消失，
    而**唯一能把它找回来的动作**（设 `ERP_ENV=dev`）会同时让放行开关重新生效——
    一个良性动机会静默重开安全缺口。`docs_enabled` 已拆成独立开关。

    这条判据管的是**下一次**：谁再把功能开关挂到 `env` 上，就会在这里红，
    从而被迫先想清楚「它会不会跟弱密钥放行绑成同一个动作」。
    """
    src = Path(__file__).resolve().parents[1] / "src" / "erp"
    pattern = re.compile(r"\b(?:settings|get_settings\(\))\.env\b")
    offenders = [
        f"{p.relative_to(src)}:{i}"
        for p in sorted(src.rglob("*.py"))
        if p.name != "settings.py"
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        f"这些地方从 Settings.env 派生行为：{offenders}。"
        "env 只用于弱密钥放行的环境判定——挂别的开关上去，就等于把那个功能的"
        "开关与「弱密钥放行」绑成同一个动作（N1 即如此）。请另立独立字段。"
    )


def test_docs_are_off_by_default() -> None:
    """Swagger 默认关：8000 内网可达而这些页面无鉴权。"""
    assert Settings(_env_file=None, **_SAFE_KWARGS).docs_enabled is False  # type: ignore[arg-type]


def _strong_env(monkeypatch: pytest.MonkeyPatch, *, docs: bool) -> None:
    for key, val in {
        "ERP_ENV": "prod",  # 部署机合并后的实际形态
        "ERP_DATABASE_URL": str(_SAFE_KWARGS["database_url"]),
        "ERP_MIGRATOR_DATABASE_URL": str(_SAFE_KWARGS["migrator_database_url"]),
        "ERP_REDIS_URL": str(_SAFE_KWARGS["redis_url"]),
        "ERP_JWT_SECRET": _STRONG,
        "ERP_CREDENTIAL_KEY": _STRONG,
        "ERP_DOCS_ENABLED": "true" if docs else "false",
    }.items():
        monkeypatch.setenv(key, val)
    get_settings.cache_clear()


@pytest.mark.parametrize("docs", [False, True])
def test_all_three_doc_endpoints_follow_the_switch(
    monkeypatch: pytest.MonkeyPatch, docs: bool
) -> None:
    """断言**端点真的没了**，不是断言那个布尔量。

    〔2026-07-27 审查 AI 的 S2〕首版只传了 `docs_url`，而 FastAPI 的 `redoc_url` /
    `openapi_url` 有默认值——`/redoc` 与 `/openapi.json` 原样开着，「关掉接口文档」
    只做到三分之一，换个路径照样把全部端点、参数、schema 端出去。
    而当时那条用例断言的是 `settings.docs_enabled is False`，**两扇门开着它照样绿**：
    判据盯的是开关，不是效果。这条改成盯效果。
    """
    _strong_env(monkeypatch, docs=docs)
    from erp.main import create_app  # 延迟导入：要先把环境摆好

    client = TestClient(create_app())
    expected = 200 if docs else 404
    for path in ("/api/docs", "/api/redoc", "/api/openapi.json"):
        assert client.get(path).status_code == expected, f"{path} 与开关不一致"
    # 顺带钉住 FastAPI 的两个默认路径不会另开一扇门
    for legacy in ("/redoc", "/openapi.json"):
        assert client.get(legacy).status_code == 404, f"{legacy} 仍可访问"


def test_startup_refusal_leaks_no_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """拒绝启动的报错里不许出现任何密钥取值。

    〔2026-07-27 审查 AI 的 S5〕`ValidationError` 的字符串表示带 `input_value=`，
    pydantic 截断中段但**保留首尾**，实测把某个强随机密钥的尾部约 22 字符带了出来。
    而这条报错正是部署指令让人**整段贴回**的那一条（铁律 2「不输出密钥」），
    它还会进容器日志、CI 日志、聊天记录。故 `get_settings()` 改抛 `SystemExit`，
    只放出校验器写的那句人读消息。
    """
    _strong_env(monkeypatch, docs=False)
    monkeypatch.setenv("ERP_CREDENTIAL_KEY", "dev-only-change-me")  # 只坏这一项
    get_settings.cache_clear()

    with pytest.raises(SystemExit) as excinfo:
        get_settings()
    msg = str(excinfo.value)
    assert "ERP_CREDENTIAL_KEY" in msg, "报错要点名是哪一项，否则运维无从下手"
    assert _STRONG not in msg, "报错里出现了强密钥全文"
    assert _STRONG[-22:] not in msg, "报错里出现了强密钥尾部（pydantic 截断保留首尾）"
    assert "input_value" not in msg, "pydantic 的原始表示不该冒出来"


def test_findings_are_specific_not_boolean() -> None:
    """自检要说清是哪一项——只回 True/False 的话，部署机拿不到可执行的下一步。"""
    s = Settings(_env_file=None, allow_insecure_defaults=True)  # type: ignore[call-arg]
    found = s.insecure_findings()
    assert len(found) == 5
    assert all(f.startswith("ERP_") for f in found)
