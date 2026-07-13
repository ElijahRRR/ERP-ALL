"""LLM 调用层：输入哈希缓存 + 用量记账（移植源 integrations/llm_cache.py + usage_logger.py）。

- 缓存键 = sha256(model + messages(sort_keys) + temperature + max_tokens)——
  "不缓存产品，缓存输入"：产品内容变了 messages 自然变，不会拿到旧结果。
- 命中路径 = 单条 UPDATE hit_count+1 RETURNING（原子），命中也写 usage 行
  （cost=0, cache_hit=true）——保真实调用画像（001 §05，源仓纪律）。
- 单价表在 system_config `llm.pricing`（{model: {input_per_1m, output_per_1m}}），
  禁止写死；缺单价按 0 记账并 warn。
- API key 只走环境（Settings.llm_api_key）；HTTP 层可注入替身（测试离线）。
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.errors import BusinessError
from erp.core.settings import get_settings

log = structlog.get_logger()


def cache_key(
    model: str, messages: list[dict[str, Any]], temperature: float, max_tokens: int
) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def _cache_get(session: AsyncSession, key: str) -> tuple[str, dict[str, Any]] | None:
    row = (
        await session.execute(
            text(
                "UPDATE app.llm_cache"
                " SET hit_count = hit_count + 1, last_hit_at = now()"
                " WHERE cache_key = :k"
                " RETURNING response_text, response_meta"
            ),
            {"k": key},
        )
    ).one_or_none()
    return (row.response_text, row.response_meta) if row else None


async def _cache_put(
    session: AsyncSession, key: str, model: str, response_text: str, meta: dict[str, Any]
) -> None:
    await session.execute(
        text(
            "INSERT INTO app.llm_cache (cache_key, model, response_text, response_meta)"
            " VALUES (:k, :m, :t, cast(:meta AS jsonb))"
            " ON CONFLICT (cache_key) DO NOTHING"
        ),
        {"k": key, "m": model, "t": response_text, "meta": json.dumps(meta, ensure_ascii=False)},
    )


async def _price(
    session: AsyncSession, model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    row = (
        await session.execute(text("SELECT value FROM app.system_config WHERE key = 'llm.pricing'"))
    ).scalar_one_or_none()
    pricing = (row or {}).get(model)
    if not pricing:
        log.warning("llm.pricing_missing", model=model)
        return 0.0
    return round(
        prompt_tokens * float(pricing.get("input_per_1m", 0)) / 1_000_000
        + completion_tokens * float(pricing.get("output_per_1m", 0)) / 1_000_000,
        6,
    )


async def log_usage(
    session: AsyncSession,
    *,
    team_id: int | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    cache_hit: bool,
    object_type: str | None = None,
    object_id: int | None = None,
    module: str = "audit",
) -> None:
    await session.execute(
        text(
            "INSERT INTO app.llm_usage_log"
            " (team_id, module, model, prompt_tokens, completion_tokens,"
            "  cost_usd, cache_hit, object_type, object_id)"
            " VALUES (:t, :mod, :m, :pt, :ct, :c, :h, :ot, :oi)"
        ),
        {
            "t": team_id,
            "mod": module,
            "m": model,
            "pt": prompt_tokens,
            "ct": completion_tokens,
            "c": cost_usd,
            "h": cache_hit,
            "ot": object_type,
            "oi": object_id,
        },
    )


class LlmClient:
    """OpenAI 兼容 chat 客户端（deepseek/dashscope 均可），带缓存与记账。

    三段原语（RS-03a：审核主链的 HTTP 期间不得持有事务/行锁）：
      check_cache（事务内，短）→ call_provider（**无任何 DB 交互**）→ record_result（新事务）。
    chat() = 三段的单事务组合，保留给非锁敏感调用方与测试（HTTP 发生在调用方事务内）。
    """

    def __init__(self) -> None:
        self._transport_factory: Any = None  # 测试注入 httpx.MockTransport

    async def check_cache(
        self,
        session: AsyncSession,
        *,
        key: str,
        model: str,
        team_id: int | None = None,
        object_type: str | None = None,
        object_id: int | None = None,
        cacheable: Callable[[str], bool] | None = None,
        module: str = "audit",
    ) -> str | None:
        """缓存查询：命中记 usage(cost=0)；命中存量坏行（不过 cacheable）→ 驱逐返 None。

        cacheable：业务侧响应校验（评审 round-2 R2-21）。坏响应一旦缓存会被同输入
        重审永久复放、无法自愈——查/写两侧都过此谓词。module：usage 归因域（默认 audit；
        L1-b 类目复排传 category_map，001 §05）。
        """
        cached = await _cache_get(session, key)
        if cached is not None and cacheable is not None and not cacheable(cached[0]):
            await session.execute(
                text("DELETE FROM app.llm_cache WHERE cache_key = :k"), {"k": key}
            )
            log.warning("llm.cache_evict_invalid", key=key, model=model)
            return None
        if cached is None:
            return None
        await log_usage(
            session,
            team_id=team_id,
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            cache_hit=True,
            object_type=object_type,
            object_id=object_id,
            module=module,
        )
        return cached[0]

    async def call_provider(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 1200,
    ) -> tuple[str, int, int]:
        """纯网络调用 → (content, prompt_tokens, completion_tokens)。

        **不接触 DB**——调用方必须在事务外调用（RS-03a 行锁纪律）。失败抛异常，
        由调用方 fail-closed 落痕（评审 R2-20）。
        """
        settings = get_settings()
        if not settings.llm_api_key and self._transport_factory is None:
            raise BusinessError("LLM_KEY_MISSING", "未配置 LLM API Key（ERP_LLM_API_KEY）")
        transport = self._transport_factory() if self._transport_factory else None
        async with httpx.AsyncClient(
            base_url=settings.llm_api_base,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=120,
            transport=transport,
        ) as client:
            resp = await client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                },
            )
        if resp.status_code != httpx.codes.OK:
            raise BusinessError(
                "LLM_CALL_FAILED",
                f"LLM 调用失败 HTTP {resp.status_code}",
                {"body": resp.text[:500]},
            )
        data = resp.json()
        content = str(data["choices"][0]["message"]["content"])
        usage = data.get("usage") or {}
        return (
            content,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )

    async def record_result(
        self,
        session: AsyncSession,
        *,
        key: str,
        model: str,
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        team_id: int | None = None,
        object_type: str | None = None,
        object_id: int | None = None,
        cacheable: Callable[[str], bool] | None = None,
        module: str = "audit",
    ) -> float:
        """真调用记账：计价 + （通过 cacheable 才）写缓存 + usage 行 → cost_usd。"""
        cost = await _price(session, model, prompt_tokens, completion_tokens)
        if cacheable is None or cacheable(content):
            await _cache_put(
                session,
                key,
                model,
                content,
                {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            )
        else:
            log.warning("llm.response_not_cacheable", model=model, head=content[:120])
        await log_usage(
            session,
            team_id=team_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            cache_hit=False,
            object_type=object_type,
            object_id=object_id,
            module=module,
        )
        return cost

    async def chat(
        self,
        session: AsyncSession,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 1200,
        team_id: int | None = None,
        object_type: str | None = None,
        object_id: int | None = None,
        cacheable: Callable[[str], bool] | None = None,
        module: str = "audit",
    ) -> tuple[str, float, bool]:
        """→ (response_text, cost_usd, cache_hit)。三段原语的单事务组合。"""
        key = cache_key(model, messages, temperature, max_tokens)
        hit = await self.check_cache(
            session,
            key=key,
            model=model,
            team_id=team_id,
            object_type=object_type,
            object_id=object_id,
            cacheable=cacheable,
            module=module,
        )
        if hit is not None:
            return hit, 0.0, True
        content, pt, ct = await self.call_provider(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        cost = await self.record_result(
            session,
            key=key,
            model=model,
            content=content,
            prompt_tokens=pt,
            completion_tokens=ct,
            team_id=team_id,
            object_type=object_type,
            object_id=object_id,
            cacheable=cacheable,
            module=module,
        )
        return content, cost, False


llm_client = LlmClient()
