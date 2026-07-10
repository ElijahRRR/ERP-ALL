"""环境配置（三层优先级：环境变量 > .env > 默认值）。

密钥类一律走环境变量，永不入库、不入 git；业务参数不放这里
（业务参数属于 system_config / team_config / automation_policy，见 R1-02 ConfigService）。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERP_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev / test / prod
    debug: bool = False

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

    log_json: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
