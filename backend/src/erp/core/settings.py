"""环境配置（三层优先级：环境变量 > .env > 默认值）。

密钥类一律走环境变量，永不入库、不入 git；业务参数不放这里
（业务参数属于 system_config / team_config / automation_policy，见 R1-02 ConfigService）。

**启动自检（RS-02a / D-Q68，2026-07-27）**：本文件里的默认值全是 `dev-only-change-me`
这类占位串，写的时候就打算让部署方换掉——但「打算」不是机制。审计侧 2026-07-27 实测
发现部署机一直带着这些默认值在跑，而库里存着全部店铺的 Walmart 凭证。故加 `_guard`：
**认出已知默认值就拒绝构造 Settings**，任何入口（api / beat / migrate / 各 tools）
一视同仁起不来，而不是打条日志继续跑。

放行必须显式声明 `ERP_ALLOW_INSECURE_DEFAULTS=1`，**且仅在 `ERP_ENV ∈ {dev, test}`
时该声明才有效**（白名单，见 `_ALLOW_ENV_VALUES`）。方向是「默认拒绝、放行留痕」——
反过来做（默认放行、危险时告警）就是这条门禁想根治的那个形态。

〔2026-07-27 审查 AI 的 S1 修正〕首版写的是「`ERP_ENV=prod` 下声明无效」的**黑名单**，
两层都虚：compose 那时根本没注入 `ERP_ENV`，部署机上 `env` 恒为 `"dev"`，那层保护从未
生效过；判据又是精确匹配，`production`/`Prod` 任一写法即失效。现在 compose 注入
`ERP_ENV: ${ERP_ENV:-prod}`（缺省即 prod）＋ 代码侧改白名单，两层才都是活的。
"""

from functools import lru_cache
from urllib.parse import urlsplit

import structlog
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ALLOW_ENV = "ERP_ALLOW_INSECURE_DEFAULTS"

# 只有这两种环境允许带着弱密钥跑（一次性的 CI 容器与本地开发库）。**白名单**：
# 取值不在表里——包括拼错的 "production"、大小写变体、以及部署机默认的 "prod"
# ——一律拒绝放行。见 `_refuse_known_insecure_defaults` 的注释。
_ALLOW_ENV_VALUES = frozenset({"dev", "test"})

# 已知弱值。判据是**值本身**，不是「等于本文件的默认值」——后者会被「把默认值改成
# 另一个人人皆知的串」绕开。
_INSECURE_SECRETS = frozenset(
    {
        "dev-only-change-me",
        "dev-only-change-me-padding-to-32-bytes!",
        "change-me",
        "changeme",
        "secret",
        "password",
        "test",
    }
)
# 库口令：pg-init 历史上把三业务角色的口令写死成角色名，postgres/postgres 则是镜像默认。
_INSECURE_DB_PASSWORDS = frozenset(
    {"postgres", "erp_app", "erp_migrator", "portal_app", "password", "changeme", ""}
)


def _dsn_password(dsn: str) -> str | None:
    """→ DSN 里的口令；解析不出来时返回 None（当作「没设口令」处理）。"""
    try:
        return urlsplit(dsn).password
    except ValueError:  # 畸形 DSN：交给真正的连接错误去报，不在这里冒充判据
        return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERP_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev / test / prod
    debug: bool = False

    # 放行开关：见模块头注。prod 下无效。
    allow_insecure_defaults: bool = False

    # 数据库（同库三角色，应用默认 erp_app；alembic 用 migrator URL）
    database_url: str = "postgresql+psycopg://erp_app:erp_app@localhost:5432/erp_all"
    migrator_database_url: str = (
        "postgresql+psycopg://erp_migrator:erp_migrator@localhost:5432/erp_all"
    )

    redis_url: str = "redis://localhost:6379/0"

    # 认证（R1-04 使用；prod 必须由环境注入，dev 默认值仅本地）
    jwt_secret: str = "dev-only-change-me-padding-to-32-bytes!"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 604800

    # 凭证加密密钥（pgcrypto 对称密钥，R1-08 使用）
    credential_key: str = "dev-only-change-me"

    # LLM（R1-10 审核 L3；key 只走环境，模型/温度等业务参数在 audit_policy.config）
    llm_api_base: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""

    # 渠道网关基址（E2E/联调可指向本地 mock；生产保持官方域名）
    channel_base_url: str = "https://marketplace.walmartapis.com"

    log_json: bool = True

    def insecure_findings(self) -> list[str]:
        """→ 仍为已知弱值的配置项清单（人读的短句），全干净时为空。

        公开方法：`GET /healthz` 之外的运维自检、测试与部署脚本都直接调它，
        免得各处重写一套判据后互相漂移。
        """
        found: list[str] = []
        if self.jwt_secret.strip().lower() in _INSECURE_SECRETS:
            found.append("ERP_JWT_SECRET 仍是占位默认值")
        if self.credential_key.strip().lower() in _INSECURE_SECRETS:
            found.append("ERP_CREDENTIAL_KEY 仍是占位默认值（凭证加密密钥）")
        for label, dsn in (
            ("ERP_DATABASE_URL", self.database_url),
            ("ERP_MIGRATOR_DATABASE_URL", self.migrator_database_url),
        ):
            pw = _dsn_password(dsn)
            if pw is None or pw.lower() in _INSECURE_DB_PASSWORDS:
                found.append(f"{label} 里的库口令仍是默认值/为空")
        # 与上面两条库 DSN 同判据：既查「有没有」也查「是不是弱值」。
        # 〔2026-07-27 审查 AI 的 S3〕原来只查 `is None`，于是 `redis://:@host` 与
        # `redis://:changeme@host` 都判成安全——同样两个值出现在库 DSN 里却会被抓。
        # compose 侧 `${REDIS_PASSWORD:?}` 对未设与空值都报错，但 ERP_REDIS_URL
        # 可以被直接导出（tools / 本地 / 部署机手工），那条口子是开的。
        redis_pw = _dsn_password(self.redis_url)
        if redis_pw is None or redis_pw.lower() in _INSECURE_DB_PASSWORDS:
            found.append("ERP_REDIS_URL 无口令或口令为已知弱值——Redis 未设 requirepass")
        return found

    @model_validator(mode="after")
    def _refuse_known_insecure_defaults(self) -> "Settings":
        found = self.insecure_findings()
        if not found:
            return self
        # 白名单而非黑名单（2026-07-27 审查 AI 的 S1）。原写 `self.env != "prod"`，
        # 两个洞：①`ERP_ENV=production`/`Prod`/`PROD` 任一写法都让 prod 保护失效；
        # ②compose 当时**根本没注入 ERP_ENV**，部署机上 env 恒为 "dev"，那层保护
        # 从未生效过。现在未知/拼错的取值一律拒绝，方向是 fail-closed。
        allowed = self.allow_insecure_defaults and self.env in _ALLOW_ENV_VALUES
        if allowed:
            # 放行也要留痕：这行日志是「谁在带着弱口令跑」的唯一线索
            structlog.get_logger().warning(
                "insecure_defaults_allowed", findings=found, env=self.env
            )
            return self
        # 提示语不能反过来教人绕过（同 S1）。原文写「ERP_ENV=prod 下无效」，而部署机
        # 那时恰好 env=dev——半夜 `make up` 起不来的人读到那句，合理推论就是「我这台
        # 不是 prod，那我能用」，于是门禁在唯一绝不该放行的机器上教操作员关掉自己。
        # 现在只在**确实可放行的环境**里才提这个开关。
        if self.env in _ALLOW_ENV_VALUES:
            hint = f"设 {_ALLOW_ENV}=1 可放行——仅限 CI 与本地开发的一次性环境"
        else:
            hint = (
                f"当前 ERP_ENV={self.env!r}，{_ALLOW_ENV} 在此无效，必须换成真实密钥"
                f"（放行仅在 ERP_ENV ∈ {sorted(_ALLOW_ENV_VALUES)} 时可用）"
            )
        raise ValueError(
            "拒绝启动：检出已知默认/弱密钥 —— " + "；".join(found) + f"。{hint}。"
            "部署机换密钥的执行顺序见 .agent/evidence/RS-02a/deploy-rotate-secrets.md"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
