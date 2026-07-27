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

from erp.core.settings import _ALLOW_ENV_VALUES

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "infra" / "docker-compose.yml"

# 直连即绕过登录/权限/RLS/审计四层的后端口。8000（api）不在内：它背后有那四层，
# D-Q68 明确把它留在内网可达，改动归 RS-02b。
_MUST_BE_LOOPBACK = {"5432", "6379"}

# 引号可有可无——compose 短语法两种写法都合法。
# 〔2026-07-27 审查 AI 的 S2〕原正则强制带引号，写成 `- 5432:5432` 就匹配不上，
# 于是 `m is None → continue`，判据**静默跳过、测试照绿**。而「本地调试临时放开
# 5432」正是最可能顺手改写法的场景，恰恰是这条判据存在的理由。
_PORT_LINE = re.compile(
    r'^\s*-\s*"?(?:(?P<host_ip>[\d.]+):)?(?P<host>\d+):(?P<container>\d+)"?\s*$'
)

# 本 compose 里必须收在 loopback 的端口各出现一次 → 共 2 条。数量自检见下。
_EXPECTED_LOOPBACK_LINES = 2

# `${VAR:-兜底}` 形态在密钥上等于 fail-open。按变量名模式扫，故意扫得宽——
# 下面的豁免表是唯一出口，新增密钥变量想漏掉判据必须先在这里留名。
_SECRETISH = re.compile(r"\$\{([A-Z_]*(?:PASSWORD|SECRET|KEY)[A-Z_]*)(:-)")

# 环境变量里的密钥类键：值必须是 `${...}` 注入，不许字面量。
# 这是**正向判据**——黑名单只挡得住「退回到旧的那几个弱值」，挡不住「新写死一个」。
_SECRET_ENV_LINE = re.compile(
    r"^\s*(?P<key>[A-Z_]*(?:PASSWORD|SECRET|KEY|AUTH|TOKEN)[A-Z_]*)\s*:\s*(?P<val>\S.*?)\s*$"
)

# DSN 里的口令位（`scheme://user:口令@host`）。同样是正向判据：口令位必须含 `${`。
_DSN_CRED = re.compile(r"://(?P<user>[^:/@\s]*):(?P<pw>[^@]*)@")

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
    matched: list[str] = []
    offenders: list[str] = []
    for raw in _compose_code().splitlines():
        m = _PORT_LINE.match(raw)
        if m is None or m.group("container") not in _MUST_BE_LOOPBACK:
            continue
        matched.append(raw.strip())
        if m.group("host_ip") != "127.0.0.1":
            offenders.append(raw.strip())
    assert not offenders, (
        f"db/redis 的宿主机端口又绑到全网了（内网任一设备可直连库、绕过全部四层管控）：{offenders}"
    )
    # 数量自检：判据认得出的行少了，说明**写法变了**，不是端口变安全了。
    # 没有这一条，把端口改成长语法（target/published）就能让上面的循环一条都不匹配、
    # 测试照绿——判据静默失效比判据不存在更糟，因为它还在装作有人看着。
    assert len(matched) == _EXPECTED_LOOPBACK_LINES, (
        f"只认出 {len(matched)} 条 db/redis 端口映射（应 {_EXPECTED_LOOPBACK_LINES} 条）："
        f"{matched}。端口写法若确实改了（如改用长语法 target/published），"
        "请同步改 _PORT_LINE 与本计数，别让判据空转。"
    )


def test_ports_use_short_syntax_only() -> None:
    """端口一律短语法——上面那条判据的前提，写在判据里而不是留在脑子里。

    改用长语法（`target:` / `published:` / `host_ip:`）本身没错，但那会让
    `_PORT_LINE` 一条都匹配不上。这里把「前提没变」也钉成判据。
    """
    long_syntax = [
        line.strip()
        for line in _compose_code().splitlines()
        if re.match(r"^\s*(target|published|host_ip)\s*:", line)
    ]
    assert not long_syntax, f"出现端口长语法 {long_syntax}——_PORT_LINE 只认短语法，请一并更新判据"


def test_no_hardcoded_secrets_in_compose() -> None:
    """口令一律 `${VAR:?}` 注入，不许有字面量回退（黑名单：防退回到旧的那几个弱值）。"""
    code = _compose_code()
    banned = {
        "POSTGRES_PASSWORD: postgres": "PG 超管默认口令写死",
        "erp_app:erp_app@": "应用 DSN 里写死默认口令",
        "erp_migrator:erp_migrator@": "迁移 DSN 里写死默认口令",
        "dev-only-change-me": "占位密钥写死",
    }
    hits = [why for needle, why in banned.items() if needle in code]
    assert not hits, f"compose 里出现写死的弱口令：{hits}"


def test_secret_values_are_injected_not_literal() -> None:
    """**正向判据**：密钥类的值必须来自 `${...}`，不许是字面量。

    〔2026-07-27 审查 AI 的 S2〕上面那条黑名单只挡得住「退回到旧的那几个弱值」，
    **挡不住新写死一个**——有人加 `ERP_JWT_SECRET: hunter2`，黑名单认不出它，
    `_SECRETISH` 也只盯 `${NAME:-` 这个形态。判据面必须反过来定：凡是密钥，
    值就得是注入的。
    """
    offenders: list[str] = []
    for raw in _compose_code().splitlines():
        m = _SECRET_ENV_LINE.match(raw)
        if m is not None and not m.group("val").startswith("${"):
            offenders.append(f"{m.group('key')} = {m.group('val')[:40]}")
        # DSN 的口令位单独看：键名（ERP_DATABASE_URL）本身不含 PASSWORD/SECRET
        for dsn in _DSN_CRED.finditer(raw):
            if dsn.group("pw") and "${" not in dsn.group("pw"):
                offenders.append(f"DSN 口令位写死：{raw.strip()[:60]}")
    assert not offenders, f"这些密钥不是注入的，是写死在 compose 里的：{offenders}"


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


def test_compose_pins_erp_env_to_a_non_permissive_default() -> None:
    """compose 必须注入 `ERP_ENV`，且缺省值不得落在放行白名单里。

    〔2026-07-27 审查 AI 的 S1〕这条判据补的是一个**从未生效过**的保护：代码侧写着
    「白名单之外不许放行弱密钥」，而 compose 压根没注入 `ERP_ENV`，部署机上
    `Settings.env` 恒为默认的 `"dev"`——正好落在白名单里。两处各自看都对，合起来是空的。
    判据直接引用 `_ALLOW_ENV_VALUES`，两侧再改也不会各说各话。
    """
    m = re.search(r"^\s*ERP_ENV\s*:\s*(?P<val>\S+)", _compose_code(), re.M)
    assert m is not None, (
        "compose 的 backend_env 没注入 ERP_ENV —— 代码侧的环境白名单会因此永远看到 "
        '默认值 "dev"，即「放行开关在部署机上一直可用」'
    )
    default = re.fullmatch(r"\$\{ERP_ENV:-(?P<d>[^}]*)\}", m.group("val"))
    assert default is not None, f"ERP_ENV 应写成 ${{ERP_ENV:-<缺省>}}，实为 {m.group('val')}"
    assert default.group("d") not in _ALLOW_ENV_VALUES, (
        f"ERP_ENV 的缺省值 {default.group('d')!r} 落在放行白名单 "
        f"{sorted(_ALLOW_ENV_VALUES)} 里——不设变量就等于默认允许弱密钥"
    )


@pytest.mark.parametrize("path", sorted((_ROOT / "infra" / "pg-init").glob("*")))
def test_pg_init_has_no_literal_role_passwords(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"PASSWORD\s+'", text), (
        f"{path.name} 里有写死的角色口令——口令要从环境变量取（见 02-roles.sh）"
    )
