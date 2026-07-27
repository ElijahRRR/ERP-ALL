"""环境配置（三层优先级：环境变量 > .env > 默认值）。

密钥类一律走环境变量，永不入库、不入 git；业务参数不放这里
（业务参数属于 system_config / team_config / automation_policy，见 R1-02 ConfigService）。

**启动自检（RS-02a / D-Q68，2026-07-27）**：本文件里的默认值全是 `dev-only-change-me`
这类占位串，写的时候就打算让部署方换掉——但「打算」不是机制。审计侧 2026-07-27 实测
发现部署机一直带着这些默认值在跑，而库里存着全部店铺的 Walmart 凭证。故加 `_guard`：
**认出已知默认值就拒绝构造 Settings**，任何入口（api / beat / migrate / 各 tools）
一视同仁起不来，而不是打条日志继续跑。

放行必须显式声明 `ERP_ALLOW_INSECURE_DEFAULTS=1`（CI 与本地开发用），
且 `ERP_ENV=prod` 下**声明也无效**。方向是「默认拒绝、放行留痕」——
反过来做（默认放行、危险时告警）就是这条门禁想根治的那个形态。
"""

from functools import lru_cache
from urllib.parse import urlsplit

import structlog
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ALLOW_ENV = "ERP_ALLOW_INSECURE_DEFAULTS"

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
        if _dsn_password(self.redis_url) is None:
            found.append("ERP_REDIS_URL 无口令——Redis 未设 requirepass")
        return found

    @model_validator(mode="after")
    def _refuse_known_insecure_defaults(self) -> "Settings":
        found = self.insecure_findings()
        if not found:
            return self
        allowed = self.allow_insecure_defaults and self.env != "prod"
        if allowed:
            # 放行也要留痕：这行日志是「谁在带着弱口令跑」的唯一线索
            structlog.get_logger().warning(
                "insecure_defaults_allowed", findings=found, env=self.env
            )
            return self
        hint = (
            f"设 {_ALLOW_ENV}=1 可放行（仅限 CI/本地开发；ERP_ENV=prod 下无效）"
            if self.env != "prod"
            else f"ERP_ENV=prod 下 {_ALLOW_ENV} 无效，必须换掉真实密钥"
        )
        raise ValueError(
            "拒绝启动：检出已知默认/弱密钥 —— " + "；".join(found) + f"。{hint}。"
            "部署机换密钥的执行顺序见 .agent/evidence/RS-02a/deploy-rotate-secrets.md"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
