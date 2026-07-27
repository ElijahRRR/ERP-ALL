"""compose 加固的**反向不变量**（RS-02a / D-Q68，2026-07-27）。

改配置容易，改回去更容易——一次「本地调试临时放开 5432」忘了改回来，门就又开了，
而这种回退没有任何运行期信号。故把「门关着」这件事本身钉成 CI 判据：
**谁把 db/redis 的宿主机端口挪回 0.0.0.0、或把口令写死回配置文件，这里就红。**

读原文而不解析 YAML：判据要盯的是端口串、`${VAR:?}` 形态这些**写法**，YAML 解析会把
它们规范化掉；而且 pyyaml 只是 uvicorn[standard] 带进来的传递依赖，不是 backend 的
声明依赖，判据不该建在这上面。
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "infra" / "docker-compose.yml"

# 直连即绕过登录/权限/RLS/审计四层的后端口。8000（api）不在内：它背后有那四层，
# D-Q68 明确把它留在内网可达，改动归 RS-02b。
_MUST_BE_LOOPBACK = {"5432", "6379"}

_PORT_LINE = re.compile(r'^\s*-\s*"(?:(?P<host_ip>[\d.]+):)?(?P<host>\d+):(?P<container>\d+)"')

# `${VAR:-兜底}` 形态在密钥上等于 fail-open。按变量名模式扫，故意扫得宽——
# 下面的豁免表是唯一出口，新增密钥变量想漏掉判据必须先在这里留名。
_SECRETISH = re.compile(r"\$\{([A-Z_]*(?:PASSWORD|SECRET|KEY)[A-Z_]*)(:-)")

# 名字里带 KEY 但不是密钥的变量。豁免必须写清理由。
_NOT_ACTUALLY_SECRET = {
    "ERP_WORKER_NODE_KEY": "采集节点的名字（默认 erp-scraper-1），不是凭证",
}


def _compose_text() -> str:
    assert _COMPOSE.is_file(), f"找不到 {_COMPOSE}"
    return _COMPOSE.read_text(encoding="utf-8")


def _compose_code() -> str:
    """去掉 `#` 注释后的 compose 原文。

    「写死的口令」「放行开关」这两条判据只该盯**生效的配置**：注释里提一句
    `dev-only-change-me` 是在解释门禁本身，不是把密钥写死。
    （本文件里没有含 `#` 的字面量值，简单按行截断即可。）
    """
    return "\n".join(line.split("#", 1)[0] for line in _compose_text().splitlines())


def test_db_and_redis_ports_bind_loopback_only() -> None:
    offenders: list[str] = []
    for raw in _compose_text().splitlines():
        m = _PORT_LINE.match(raw)
        if m is None or m.group("container") not in _MUST_BE_LOOPBACK:
            continue
        if m.group("host_ip") != "127.0.0.1":
            offenders.append(raw.strip())
    assert not offenders, (
        f"db/redis 的宿主机端口又绑到全网了（内网任一设备可直连库、绕过全部四层管控）：{offenders}"
    )


def test_no_hardcoded_secrets_in_compose() -> None:
    """口令一律 `${VAR:?}` 注入，不许有字面量回退。"""
    code = _compose_code()
    banned = {
        "POSTGRES_PASSWORD: postgres": "PG 超管默认口令写死",
        "erp_app:erp_app@": "应用 DSN 里写死默认口令",
        "erp_migrator:erp_migrator@": "迁移 DSN 里写死默认口令",
        "dev-only-change-me": "占位密钥写死",
    }
    hits = [why for needle, why in banned.items() if needle in code]
    assert not hits, f"compose 里出现写死的弱口令：{hits}"


def test_password_vars_have_no_default_fallback() -> None:
    """`${VAR:-default}` 形态等于「缺变量就用兜底值」——那正是本单要根治的 fail-open。

    密钥类变量必须是 `${VAR:?...}`：缺了就当场拒起。
    """
    bad = [
        name for name, _ in _SECRETISH.findall(_compose_code()) if name not in _NOT_ACTUALLY_SECRET
    ]
    assert not bad, f"这些密钥变量带了默认回退，缺变量时会静默用弱值：{bad}"


def test_secret_var_exemptions_are_not_zombies() -> None:
    """豁免表不许养僵尸：被豁免的变量必须还在 compose 里。

    否则「当年为某个变量开的口子」会一直挂着，等某天有人恰好新增同名变量时静默生效
    ——这类过期豁免正是让门禁慢慢失效的方式。
    """
    code = _compose_code()
    stale = sorted(name for name in _NOT_ACTUALLY_SECRET if name not in code)
    assert not stale, f"这些豁免的变量已不在 compose 中，请删掉豁免：{stale}"


def test_redis_requires_auth() -> None:
    code = _compose_code()
    assert "--requirepass" in code, "redis 没设 requirepass"
    # 未认证的 PING 返回 NOAUTH 但退出码为 0 → 只跑 `redis-cli ping` 的 healthcheck
    # 会把「认证没配对」判成健康。必须验回显。
    assert "grep -q PONG" in code, "redis healthcheck 没验回显，认证配错时仍会判健康"


def test_compose_does_not_grant_the_insecure_escape_hatch() -> None:
    """放行开关只给 CI 与本地开发；一旦进了 compose，部署机就又能带着弱密钥跑。"""
    assert "ERP_ALLOW_INSECURE_DEFAULTS" not in _compose_code()


@pytest.mark.parametrize("path", sorted((_ROOT / "infra" / "pg-init").glob("*")))
def test_pg_init_has_no_literal_role_passwords(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"PASSWORD\s+'", text), (
        f"{path.name} 里有写死的角色口令——口令要从环境变量取（见 02-roles.sh）"
    )
