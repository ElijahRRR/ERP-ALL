"""identity 域 API 模型（与 002 契约字段对齐；契约为准，缺口提单改契约）。"""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class RoleOut(BaseModel):
    id: int
    team_id: int | None
    name: str
    description: str | None
    permission_codes: list[str] = []


class UserOut(BaseModel):
    id: int
    team_id: int | None
    username: str
    display_name: str
    is_super: bool
    status: str
    roles: list[RoleOut] = []
    last_login_at: datetime | None


class MeOut(BaseModel):
    user: UserOut
    permissions: list[str]


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    team_id: int | None = None  # 仅超管可指定；普通管理员固定本团队


class UserPatch(BaseModel):
    display_name: str | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled|locked)$")
    password: str | None = Field(default=None, min_length=8, max_length=128)


class RoleIdsIn(BaseModel):
    role_ids: list[int]


class RoleWrite(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None


class PermissionCodesIn(BaseModel):
    permission_codes: list[str]


class PermissionOut(BaseModel):
    code: str
    module: str
    name: str


class TeamOut(BaseModel):
    id: int
    name: str
    status: str
    created_at: datetime


class TeamWrite(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


class AuditLogOut(BaseModel):
    id: int
    occurred_at: datetime
    actor_type: str
    actor_id: int | None
    # 操作人显示名（R2-14 14b 验收③）：在册用户=display_name；已删用户=「label（已删除）」
    # （墓碑解析）；主表墓碑都查不到、或非 user 操作者 = None（前端回落 actor_type#id）。
    actor_label: str | None = None
    action: str
    object_type: str
    object_id: str
    before: dict | None  # type: ignore[type-arg]
    after: dict | None  # type: ignore[type-arg]
    ip: str | None


class DictItemOut(BaseModel):
    code: str
    label: str
    sort: int
