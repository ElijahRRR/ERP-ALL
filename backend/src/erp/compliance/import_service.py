"""标准导入作业服务（specs/001 §import_job / D-Q35）。

契约：dry-run 校验 → 幂等 upsert（各表唯一键）→ 逐块行数核对 → 汇总报告；
**任何一块行数不符即 failed 并回滚该块**（lark 截断教训：逐块核对）。

已实现域：黑名单四域（brand/seller/asin/category）+ 商标（trademark → refdata.trademark）
+ 政策（policy → refdata.prohibited_policy，喂 L3 静态 37 政策 prompt）
+ 类目映射（category_map → refdata.category_map，L1 候选召回）
+ PT 元数据（pt_meta → refdata.pt_meta，L1 候选 INNER JOIN 过滤废弃 PT 的主表）。
黑名单归一化与审核 L0 查表严格一致（全部走 audit.pipeline._norm）；商标 mark_norm 与
审核 L2-R5 反查严格一致（同样 _norm），否则导入的词审核时查不到——这是必须锁死的不变量。
其余域（gtin/tro…）表结构与 job 通道已就位，导入器随各自工单补。
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.audit.pipeline import NON_BRAND_PLACEHOLDERS, _norm
from erp.compliance import assertion as assertion_svc
from erp.core.errors import BusinessError
from erp.order.checks import normalize_street, normalize_zip5


@dataclass(frozen=True)
class _Domain:
    table: str
    subject_col: str
    display_col: str | None
    subject_keys: tuple[str, ...]  # 行中主体字段名（按序取第一个非空）
    display_keys: tuple[str, ...] = ()
    skip_placeholder: bool = False  # 仅品牌：占位符（unbranded/generic…）不入黑名单
    normalizer: Callable[[str], str] | None = None  # 缺省 _norm；钓鱼域用 BR-ORD-005 口径


# 归一化统一走 _norm（lowercase + 多空格压一），与 L0 查表一致
_DOMAINS: dict[str, _Domain] = {
    "blacklist_brand": _Domain(
        "blacklist_brand",
        "brand_norm",
        "brand_display",
        subject_keys=("brand", "brand_norm", "brand_display"),
        display_keys=("brand_display", "brand"),
        skip_placeholder=True,
    ),
    "blacklist_seller": _Domain(
        "blacklist_seller",
        "seller_ref",
        "seller_name",
        subject_keys=("seller_id", "seller_ref"),
        display_keys=("seller_name",),
    ),
    "blacklist_asin": _Domain(
        "blacklist_asin",
        "asin",
        None,
        subject_keys=("asin",),
    ),
    "blacklist_category": _Domain(
        "blacklist_category",
        "category_ref",
        None,
        subject_keys=("category", "category_ref"),
    ),
    # 钓鱼黑名单（R2-05 四检 phishing；归一化与查表共用 order.checks 口径）
    "blacklist_address": _Domain(
        "blacklist_address",
        "street_norm",
        None,
        subject_keys=("street", "address", "street_norm", "地址"),
        normalizer=normalize_street,
    ),
    "blacklist_zip": _Domain(
        "blacklist_zip",
        "zip5",
        None,
        subject_keys=("zip", "zipcode", "postal_code", "邮编", "zip5"),
        normalizer=normalize_zip5,
    ),
}

# 商标域 / 政策域 / 类目映射域走各自独立代码路径（refdata.*：无 team_id、多列、各自幂等键），
# 不套黑名单 _apply_row。仍复用同一 job 通道与逐块核对（_process_chunks）。
TRADEMARK_DOMAIN = "trademark"
POLICY_DOMAIN = "policy"
CATEGORY_MAP_DOMAIN = "category_map"
PT_META_DOMAIN = "pt_meta"
PT_SPEC_DOMAIN = "pt_spec"
LISTING_ERROR_CATALOG_DOMAIN = "listing_error_catalog"
SUPPORTED_DOMAINS = (
    *_DOMAINS,
    TRADEMARK_DOMAIN,
    POLICY_DOMAIN,
    CATEGORY_MAP_DOMAIN,
    PT_META_DOMAIN,
    PT_SPEC_DOMAIN,
    LISTING_ERROR_CATALOG_DOMAIN,
)

# 商标行列名兼容（源仓 USPTO ETL 惯用名：serial_number/mark_identification/filing_date…）
_TM_SERIAL_KEYS = ("serial_no", "serial_number")
_TM_MARK_KEYS = ("mark_text", "mark_identification", "wordmark", "mark")
_TM_NORM_KEYS = ("mark_norm",)
_TM_STATUS_KEYS = ("status_code", "status")
_TM_LIVE_KEYS = ("is_live", "live_dead", "live")
_TM_NICE_KEYS = ("nice_classes", "nice_class", "nice")
_TM_OWNER_KEYS = ("owner_name", "owner")
_TM_FILED_KEYS = ("filed_date", "filing_date")
_TM_REGISTERED_KEYS = ("registered_date", "registration_date")

_LIVE_TRUE = {"live", "true", "t", "1", "yes", "y"}
_LIVE_FALSE = {"dead", "false", "f", "0", "no", "n"}


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v)
    return None


def _parse_int(row: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    """列值 → int（含浮点串 "12.0" → 12）；缺失/非数字 → None。"""
    raw = _first(row, keys)
    if raw is None:
        return None
    token = raw.strip()
    if token.lstrip("-").isdigit():
        return int(token)
    try:  # 宽容 "12.0" 这类浮点串（openpyxl 数字列常见）
        return int(float(token))
    except ValueError:
        return None


async def create_job(
    session: AsyncSession,
    *,
    domain: str,
    source_name: str,
    total_rows: int,
    fmt: str = "csv",
    source_kind: str = "file",
    chunk_size: int = 5000,
    team_id: int | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    """建导入作业（pending）。domain 必须受支持；total_rows = 调用方声明的源行数（供核对）。"""
    if domain not in SUPPORTED_DOMAINS:
        raise BusinessError(
            "IMPORT_DOMAIN_UNSUPPORTED",
            f"域 {domain} 导入器尚未实现",
            {"supported": list(SUPPORTED_DOMAINS)},
        )
    row = (
        await session.execute(
            text(
                "INSERT INTO app.import_job"
                " (team_id, domain, source_kind, source_name, format, total_rows,"
                "  chunk_size, created_by)"
                " VALUES (:t, :d, :sk, :sn, :f, :n, :cs, :u)"
                " RETURNING id, status, created_at"
            ),
            {
                "t": team_id,
                "d": domain,
                "sk": source_kind,
                "sn": source_name,
                "f": fmt,
                "n": total_rows,
                "cs": chunk_size,
                "u": created_by,
            },
        )
    ).one()
    return {"id": row.id, "status": row.status, "domain": domain, "total_rows": total_rows}


@dataclass
class _Counters:
    ok: int = 0
    skip: int = 0
    err: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


# 每行处理器：把一行分类为 ok/skip/err 并写库（黑名单/商标各自实现，共用逐块核对）
_RowApplier = Callable[[AsyncSession, dict[str, Any], int, _Counters], Awaitable[None]]


async def import_rows(
    session: AsyncSession, *, job_id: int, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """把 rows 幂等灌入目标表，逐块核对行数，更新 job 计数与状态。

    返回 {status, total, ok, skip, err, verify}。行数与声明 total_rows 不符 → 抛
    IMPORT_ROW_COUNT_MISMATCH（调用方在独立事务标 failed）。
    """
    job = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, domain, chunk_size, total_rows, status"
                    " FROM app.import_job WHERE id = :j FOR UPDATE"
                ),
                {"j": job_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if job is None:
        raise BusinessError("IMPORT_JOB_NOT_FOUND", "导入作业不存在")
    if job["status"] not in ("pending", "validating"):
        raise BusinessError("IMPORT_JOB_STATE_INVALID", f"作业状态 {job['status']} 不可导入")
    team_id = job["team_id"]
    chunk_size = job["chunk_size"] or 5000

    # 源截断守卫：声明总数 > 0 时，实际收到行数必须相符（lark 截断教训）
    if job["total_rows"] and len(rows) != job["total_rows"]:
        await session.execute(
            text("UPDATE app.import_job SET status = 'failed', finished_at = now() WHERE id = :j"),
            {"j": job_id},
        )
        raise BusinessError(
            "IMPORT_ROW_COUNT_MISMATCH",
            f"源行数不符：声明 {job['total_rows']}，实到 {len(rows)}",
            {"declared": job["total_rows"], "received": len(rows)},
        )

    await session.execute(
        text("UPDATE app.import_job SET status = 'running', started_at = now() WHERE id = :j"),
        {"j": job_id},
    )

    # 按域选每行处理器（显式分派，勿用「非黑名单即商标」的隐式回退）
    domain = job["domain"]
    dom = _DOMAINS.get(domain)

    async def apply_row(s: AsyncSession, row: dict[str, Any], line: int, c: _Counters) -> None:
        if dom is not None:
            await _apply_row(s, dom, team_id, row, line, c)
        elif domain == TRADEMARK_DOMAIN:
            await _apply_trademark_row(s, row, line, c)
        elif domain == POLICY_DOMAIN:
            await _apply_policy_row(s, row, line, c)
        elif domain == CATEGORY_MAP_DOMAIN:
            await _apply_category_map_row(s, row, line, c)
        elif domain == PT_META_DOMAIN:
            await _apply_pt_meta_row(s, row, line, c)
        elif domain == PT_SPEC_DOMAIN:
            await _apply_pt_spec_row(s, row, line, c)
        elif domain == LISTING_ERROR_CATALOG_DOMAIN:
            await _apply_listing_error_row(s, row, line, c)
        else:  # create_job 已闸，理论不达
            raise BusinessError("IMPORT_DOMAIN_UNSUPPORTED", f"域 {domain} 无处理器")

    total, verify = await _process_chunks(
        session, job_id=job_id, rows=rows, chunk_size=chunk_size, apply_row=apply_row
    )

    await session.execute(
        text(
            "UPDATE app.import_job SET status = 'done', finished_at = now(),"
            " ok_rows = :ok, skip_rows = :sk, err_rows = :er,"
            " verify = cast(:v AS jsonb) WHERE id = :j"
        ),
        {
            "ok": total.ok,
            "sk": total.skip,
            "er": total.err,
            "v": _json({"chunks": verify, "sample_errors": total.errors[:50]}),
            "j": job_id,
        },
    )
    return {
        "status": "done",
        "total": len(rows),
        "ok": total.ok,
        "skip": total.skip,
        "err": total.err,
        "verify": verify,
    }


async def _process_chunks(
    session: AsyncSession,
    *,
    job_id: int,
    rows: list[dict[str, Any]],
    chunk_size: int,
    apply_row: _RowApplier,
) -> tuple[_Counters, list[dict[str, int]]]:
    """逐块调用 apply_row + 逐块行数核对（黑名单/商标共用）。

    每行必被分类为 ok/skip/err；块内处理数≠块行数 → 该块 failed 回滚（结构性守卫）。
    """
    total = _Counters()
    verify: list[dict[str, int]] = []
    for ci in range(0, len(rows), chunk_size):
        chunk = rows[ci : ci + chunk_size]
        c = _Counters()
        for idx, row in enumerate(chunk):
            await apply_row(session, row, ci + idx, c)
        loaded = c.ok + c.skip + c.err
        verify.append({"chunk": ci // chunk_size, "expected": len(chunk), "loaded": loaded})
        if loaded != len(chunk):  # 结构性守卫：理论不触发，触发即代码缺陷
            await session.execute(
                text(
                    "UPDATE app.import_job SET status = 'failed', finished_at = now() WHERE id = :j"
                ),
                {"j": job_id},
            )
            raise BusinessError(
                "IMPORT_CHUNK_MISMATCH",
                f"块 {ci // chunk_size} 行数不符：{loaded}/{len(chunk)}",
            )
        total.ok += c.ok
        total.skip += c.skip
        total.err += c.err
        total.errors.extend(c.errors)
    return total, verify


async def _apply_row(
    session: AsyncSession,
    dom: _Domain,
    team_id: int | None,
    row: dict[str, Any],
    line: int,
    c: _Counters,
) -> None:
    raw = _first(row, dom.subject_keys)
    subject = (dom.normalizer or _norm)(raw) if raw else ""
    if not subject:
        c.err += 1
        c.errors.append({"line": line, "reason": "主体字段为空", "row": row})
        return
    if dom.skip_placeholder and subject in NON_BRAND_PLACEHOLDERS:
        c.skip += 1  # 占位符品牌（unbranded/generic…）不入黑名单，否则误挡全站
        return
    display = _first(row, dom.display_keys) or (raw if dom.display_col else None)
    reason = row.get("reason")

    # RS-04D（R2-12 增量1）：不再直写 canonical——记一条 import 源断言，canonical
    # （blacklist_* active 行）由有效断言投影维护（人工 allow 裁决可压导入源）。
    res = await assertion_svc.record_assertion(
        session,
        team_id=team_id,
        domain=dom.table.removeprefix("blacklist_"),
        subject_norm=subject,
        subject_display=display,
        source="import",
        reason=str(reason) if reason is not None else None,
    )
    if res["created"]:
        c.ok += 1
    else:
        c.skip += 1  # 幂等：同源断言已在册 → 跳过（canonical 投影仍归位）


async def _apply_trademark_row(
    session: AsyncSession, row: dict[str, Any], line: int, c: _Counters
) -> None:
    """幂等 upsert 一行到 refdata.trademark（键 serial_no）。

    err 仅两种：serial_no 或 mark_text 缺失（无 skip——ON CONFLICT 恒更新，重导即 ok，
    表内每 serial_no 恒一行）。mark_norm 契约：优先取 mark_norm 列，否则由 mark_text
    派生（_norm = 小写 + 压空格），与 L2-R5 `mark_norm = ANY(小写候选)` 严格对齐，
    否则 R5 永远查不到本行。is_live：显式列（bool/LIVE/DEAD）优先，否则按 status_code
    查 refdata.tm_status_code 字典派生（查不到 → NULL/未知）。
    """
    serial = _first(row, _TM_SERIAL_KEYS)
    mark_text = _first(row, _TM_MARK_KEYS)
    if not serial or not mark_text:
        c.err += 1
        c.errors.append({"line": line, "reason": "serial_no 或 mark_text 缺失", "row": row})
        return

    provided_norm = _first(row, _TM_NORM_KEYS)
    mark_norm = _norm(provided_norm) if provided_norm else _norm(mark_text)

    status_code = _first(row, _TM_STATUS_KEYS)
    is_live = _parse_is_live(row)
    if is_live is None and status_code:
        is_live = await _lookup_is_live(session, status_code)

    await session.execute(
        text(
            "INSERT INTO refdata.trademark"
            " (serial_no, mark_text, mark_norm, status_code, is_live, nice_classes,"
            "  owner_name, filed_date, registered_date, updated_at)"
            " VALUES (:sn, :mt, :mn, :sc, :live, cast(:nice AS smallint[]),"
            "  :owner, cast(:filed AS date), cast(:reg AS date), now())"
            " ON CONFLICT (serial_no) DO UPDATE SET"
            "  mark_text = EXCLUDED.mark_text,"
            "  mark_norm = EXCLUDED.mark_norm,"
            "  status_code = EXCLUDED.status_code,"
            "  is_live = EXCLUDED.is_live,"
            "  nice_classes = EXCLUDED.nice_classes,"
            "  owner_name = EXCLUDED.owner_name,"
            "  filed_date = EXCLUDED.filed_date,"
            "  registered_date = EXCLUDED.registered_date,"
            "  updated_at = now()"
        ),
        {
            "sn": serial,
            "mt": mark_text,
            "mn": mark_norm,
            "sc": status_code,
            "live": is_live,
            "nice": _parse_nice_classes(row),
            "owner": _first(row, _TM_OWNER_KEYS),
            "filed": _first(row, _TM_FILED_KEYS),
            "reg": _first(row, _TM_REGISTERED_KEYS),
        },
    )
    c.ok += 1


# 政策行列名兼容（飞书 OJSrkV 导出惯用名）
_POL_CAT_EN_KEYS = ("category_en", "category")
_POL_CAT_ZH_KEYS = ("category_zh", "category_cn")
_POL_STATUS_KEYS = ("overall_status", "status")
_POL_PROHIB_KEYS = ("prohibited_items", "prohibited")
_POL_COND_KEYS = ("conditional_items", "conditional")
_POL_PRE_KEYS = ("preapproval_items", "preapproval")
_POL_RISK_KEYS = ("zh_seller_risk", "seller_risk", "risk")
_POL_NOTES_KEYS = ("zh_seller_notes", "seller_notes", "notes")


async def _apply_policy_row(
    session: AsyncSession, row: dict[str, Any], line: int, c: _Counters
) -> None:
    """幂等 upsert 一行到 refdata.prohibited_policy（键 category_en）。

    err 仅一种：category_en 缺失（无 skip——ON CONFLICT 恒更新，重导即 ok）。seq 供 L3
    prompt 排序（源仓 id）；缺失置 NULL（排 NULLS LAST）。
    """
    cat_en = _first(row, _POL_CAT_EN_KEYS)
    if not cat_en:
        c.err += 1
        c.errors.append({"line": line, "reason": "category_en 缺失", "row": row})
        return
    seq_raw = row.get("seq") if row.get("seq") is not None else row.get("id")
    seq: int | None = None
    if seq_raw is not None:
        seq_str = str(seq_raw).strip()
        if seq_str.lstrip("-").isdigit():
            seq = int(seq_str)
    await session.execute(
        text(
            "INSERT INTO refdata.prohibited_policy"
            " (category_en, seq, category_zh, overall_status, prohibited_items,"
            "  conditional_items, preapproval_items, zh_seller_risk, zh_seller_notes, updated_at)"
            " VALUES (:en, :seq, :zh, :st, :pro, :cond, :pre, :risk, :notes, now())"
            " ON CONFLICT (category_en) DO UPDATE SET"
            "  seq = EXCLUDED.seq, category_zh = EXCLUDED.category_zh,"
            "  overall_status = EXCLUDED.overall_status,"
            "  prohibited_items = EXCLUDED.prohibited_items,"
            "  conditional_items = EXCLUDED.conditional_items,"
            "  preapproval_items = EXCLUDED.preapproval_items,"
            "  zh_seller_risk = EXCLUDED.zh_seller_risk,"
            "  zh_seller_notes = EXCLUDED.zh_seller_notes, updated_at = now()"
        ),
        {
            "en": cat_en,
            "seq": seq,
            "zh": _first(row, _POL_CAT_ZH_KEYS),
            "st": _first(row, _POL_STATUS_KEYS),
            "pro": _first(row, _POL_PROHIB_KEYS),
            "cond": _first(row, _POL_COND_KEYS),
            "pre": _first(row, _POL_PRE_KEYS),
            "risk": _first(row, _POL_RISK_KEYS),
            "notes": _first(row, _POL_NOTES_KEYS),
        },
    )
    c.ok += 1


# 类目映射行列名兼容（源仓 walmart_category_map 列 + 飞书「映射明细」全列导出别名）
_CM_AMAZON_KEYS = ("amazon_category", "amazon_path", "amazon_category_path", "category_path")
_CM_PT_KEYS = ("walmart_product_type", "walmart_pt", "product_type", "wpt")
_CM_CONF_KEYS = ("confidence",)
_CM_CERT_KEYS = ("requires_certificate", "requires_cert")
_CM_FORBIDDEN_KEYS = ("zh_seller_forbidden", "seller_forbidden")
_CM_REQ_KEYS = ("requirements",)
_CM_NOTES_KEYS = ("notes", "note")
# 0016 补源列（飞书映射明细无损导入）
_CM_LEAF_KEYS = ("amazon_leaf", "leaf")
_CM_NODE_KEYS = ("browse_node_id", "browse_node", "node_id")
_CM_RANK_KEYS = ("rank_no", "rank", "rank_number")
_CM_MATCH_KEYS = ("match_type", "match")
_CM_BATCH_KEYS = ("source_batch", "batch")

_FLAG_TRUE = {"true", "t", "1", "yes", "y", "是"}


def _parse_flag(row: dict[str, Any], keys: tuple[str, ...]) -> bool:
    """布尔列宽容解析（TRUE/1/是…→True；其余含缺失→False，与源表 DEFAULT FALSE 一致）。"""
    for k in keys:
        v = row.get(k)
        if v is None:
            continue
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in _FLAG_TRUE
    return False


async def _apply_category_map_row(
    session: AsyncSession, row: dict[str, Any], line: int, c: _Counters
) -> None:
    """幂等 upsert 一行到 refdata.category_map（键 (amazon_category, walmart_product_type)）。

    err 仅一种：amazon_category 或 walmart_product_type 缺失（无 skip——ON CONFLICT
    恒更新，重导即 ok）。amazon_category 保持原文（去首尾空格）——L1 与源仓一致做
    精确/前缀/叶子匹配，不做大小写归一。walmart_product_type='无对应Walmart PT'
    为合法业务条目（unmapped 标记），照导。
    """
    amazon = _first(row, _CM_AMAZON_KEYS)
    pt = _first(row, _CM_PT_KEYS)
    if not amazon or not pt:
        c.err += 1
        c.errors.append(
            {"line": line, "reason": "amazon_category 或 walmart_product_type 缺失", "row": row}
        )
        return
    await session.execute(
        text(
            "INSERT INTO refdata.category_map"
            " (amazon_category, walmart_product_type, confidence, requires_certificate,"
            "  zh_seller_forbidden, requirements, notes,"
            "  amazon_leaf, browse_node_id, rank_no, match_type, source_batch, updated_at)"
            " VALUES (:az, :pt, :conf, :cert, :forb, :req, :notes,"
            "  :leaf, :node, :rank, :match, :batch, now())"
            " ON CONFLICT (amazon_category, walmart_product_type) DO UPDATE SET"
            "  confidence = EXCLUDED.confidence,"
            "  requires_certificate = EXCLUDED.requires_certificate,"
            "  zh_seller_forbidden = EXCLUDED.zh_seller_forbidden,"
            "  requirements = EXCLUDED.requirements,"
            "  notes = EXCLUDED.notes,"
            "  amazon_leaf = EXCLUDED.amazon_leaf,"
            "  browse_node_id = EXCLUDED.browse_node_id,"
            "  rank_no = EXCLUDED.rank_no,"
            "  match_type = EXCLUDED.match_type,"
            "  source_batch = EXCLUDED.source_batch,"
            "  updated_at = now()"
        ),
        {
            "az": amazon.strip(),
            "pt": pt.strip(),
            "conf": _first(row, _CM_CONF_KEYS),
            "cert": _parse_flag(row, _CM_CERT_KEYS),
            "forb": _parse_flag(row, _CM_FORBIDDEN_KEYS),
            "req": _first(row, _CM_REQ_KEYS),
            "notes": _first(row, _CM_NOTES_KEYS),
            "leaf": _first(row, _CM_LEAF_KEYS),
            "node": _first(row, _CM_NODE_KEYS),
            "rank": _parse_int(row, _CM_RANK_KEYS),
            "match": _first(row, _CM_MATCH_KEYS),
            "batch": _first(row, _CM_BATCH_KEYS),
        },
    )
    c.ok += 1


# PT 元数据行列名兼容（飞书「沃尔玛类目」sheet 一行一 PT + 源仓 walmart_pt_meta 列）
_PT_TYPE_KEYS = ("walmart_product_type", "walmart_pt", "product_type", "wpt", "pt")
_PT_CATEGORY_KEYS = ("walmart_category", "category")
_PT_PTG_KEYS = ("walmart_ptg", "ptg", "product_type_group")
_PT_ACCESS_KEYS = ("access_state", "access")
_PT_CANDO_KEYS = ("zh_can_do", "can_do", "cando")
_PT_FORBIDDEN_KEYS = ("zh_seller_forbidden", "seller_forbidden")
_PT_REQ_KEYS = ("requirements", "require")
_PT_NOTES_KEYS = ("notes", "note")
_PT_TOTAL_KEYS = ("total_fields", "total")
_PT_REQCNT_KEYS = ("required_count", "required_cnt", "required_num")
_PT_REQFIELDS_KEYS = ("required_fields", "required")


async def _apply_pt_meta_row(
    session: AsyncSession, row: dict[str, Any], line: int, c: _Counters
) -> None:
    """幂等 upsert 一行到 refdata.pt_meta（键 walmart_product_type）。

    err 仅一种：walmart_product_type 缺失（无 skip——ON CONFLICT 恒更新，重导即 ok，
    表内每 PT 恒一行）。这是 L1 候选召回的过滤主表：category_map 召回的候选 PT 必须
    INNER JOIN pt_meta 才保留（过滤源仓 2026-05-09 教训里的废弃 PT）。zh_seller_forbidden
    NOT NULL DEFAULT false，缺失即 false，与源表一致。total_fields/required_count 供
    上架成本估计，非数字置 NULL。
    """
    pt = _first(row, _PT_TYPE_KEYS)
    if not pt:
        c.err += 1
        c.errors.append({"line": line, "reason": "walmart_product_type 缺失", "row": row})
        return
    await session.execute(
        text(
            "INSERT INTO refdata.pt_meta"
            " (walmart_product_type, walmart_category, walmart_ptg, access_state, zh_can_do,"
            "  zh_seller_forbidden, requirements, notes, total_fields, required_count,"
            "  required_fields, updated_at)"
            " VALUES (:pt, :cat, :ptg, :acc, :cando, :forb, :req, :notes, :total, :reqcnt,"
            "  :reqf, now())"
            " ON CONFLICT (walmart_product_type) DO UPDATE SET"
            "  walmart_category = EXCLUDED.walmart_category,"
            "  walmart_ptg = EXCLUDED.walmart_ptg,"
            "  access_state = EXCLUDED.access_state,"
            "  zh_can_do = EXCLUDED.zh_can_do,"
            "  zh_seller_forbidden = EXCLUDED.zh_seller_forbidden,"
            "  requirements = EXCLUDED.requirements,"
            "  notes = EXCLUDED.notes,"
            "  total_fields = EXCLUDED.total_fields,"
            "  required_count = EXCLUDED.required_count,"
            "  required_fields = EXCLUDED.required_fields,"
            "  updated_at = now()"
        ),
        {
            "pt": pt.strip(),
            "cat": _first(row, _PT_CATEGORY_KEYS),
            "ptg": _first(row, _PT_PTG_KEYS),
            "acc": _first(row, _PT_ACCESS_KEYS),
            "cando": _first(row, _PT_CANDO_KEYS),
            "forb": _parse_flag(row, _PT_FORBIDDEN_KEYS),
            "req": _first(row, _PT_REQ_KEYS),
            "notes": _first(row, _PT_NOTES_KEYS),
            "total": _parse_int(row, _PT_TOTAL_KEYS),
            "reqcnt": _parse_int(row, _PT_REQCNT_KEYS),
            "reqf": _first(row, _PT_REQFIELDS_KEYS),
        },
    )
    c.ok += 1


# PT spec 行列名兼容（源仓 walmart_pt_spec 直导 jsonl）
_PS_TYPE_KEYS = ("walmart_product_type", "walmart_pt", "product_type", "wpt")


def _jsonb_or_none(v: Any) -> str | None:
    """jsonb 列宽容：原生 list/dict → dumps；JSON 串原样；其余/空 → None。"""
    if v is None:
        return None
    if isinstance(v, list | dict):
        return json.dumps(v, ensure_ascii=False)
    s = str(v).strip()
    return s if s else None


async def _apply_pt_spec_row(
    session: AsyncSession, row: dict[str, Any], line: int, c: _Counters
) -> None:
    """幂等 upsert 一行到 refdata.pt_spec（键 walmart_product_type，R3b+R2-03 弹药）。

    err 仅一种：walmart_product_type 缺失。has_real_cert/has_soft_cert 布尔宽容
    （_parse_flag）；jsonb 列（real_cert_fields/soft_cert_fields/required_fields/fields）。
    fields=per-PT 官方 v5 原始 schema 节点（0020，extract_mp_item_spec 无损产出；
    含 '__orderable__' 伪行）——行内缺省时 COALESCE 保留库中已有值：审核子集重导
    不得清掉上架已灌的全量规格。
    """
    pt = _first(row, _PS_TYPE_KEYS)
    if not pt:
        c.err += 1
        c.errors.append({"line": line, "reason": "walmart_product_type 缺失", "row": None})
        return
    await session.execute(
        text(
            "INSERT INTO refdata.pt_spec"
            " (walmart_product_type, has_real_cert, real_cert_fields, has_soft_cert,"
            "  soft_cert_fields, total_fields, required_count, required_fields, fields,"
            "  updated_at)"
            " VALUES (:pt, :hrc, cast(:rcf AS jsonb), :hsc, cast(:scf AS jsonb),"
            "  :total, :reqcnt, cast(:reqf AS jsonb), cast(:fields AS jsonb), now())"
            " ON CONFLICT (walmart_product_type) DO UPDATE SET"
            "  has_real_cert = EXCLUDED.has_real_cert,"
            "  real_cert_fields = EXCLUDED.real_cert_fields,"
            "  has_soft_cert = EXCLUDED.has_soft_cert,"
            "  soft_cert_fields = EXCLUDED.soft_cert_fields,"
            "  total_fields = EXCLUDED.total_fields,"
            "  required_count = EXCLUDED.required_count,"
            "  required_fields = EXCLUDED.required_fields,"
            "  fields = COALESCE(EXCLUDED.fields, pt_spec.fields),"
            "  updated_at = now()"
        ),
        {
            "pt": pt.strip(),
            "hrc": _parse_flag(row, ("has_real_cert",)),
            "rcf": _jsonb_or_none(row.get("real_cert_fields")),
            "hsc": _parse_flag(row, ("has_soft_cert",)),
            "scf": _jsonb_or_none(row.get("soft_cert_fields")),
            "total": _parse_int(row, ("total_fields", "total")),
            "reqcnt": _parse_int(row, ("required_count",)),
            "reqf": _jsonb_or_none(row.get("required_fields")),
            "fields": _jsonb_or_none(row.get("fields")),
        },
    )
    c.ok += 1


# 错误码字典行（R2-03 增量 5：listing_error_catalog 批量灌入）
_EC_CODE_KEYS = ("error_code", "code")
_EC_DISPOSITIONS = ("auto_retry", "backoff_retry", "rebuild_spec", "skip", "manual", "fatal")


async def _apply_listing_error_row(
    session: AsyncSession, row: dict[str, Any], line: int, c: _Counters
) -> None:
    """幂等 upsert 一行到 app.listing_error_catalog（键 error_code，全局字典 D-Q11）。

    err：error_code 缺失 / disposition 非法（六值 CHECK，DB 拒绝前先在应用层报行号）。
    category/title 缺省宽容（'未分类'/error_code 本身）——运行时草稿行
    （_ensure_error_cataloged）同款语义，导入可覆盖草稿。
    """
    code = _first(row, _EC_CODE_KEYS)
    if not code:
        c.err += 1
        c.errors.append({"line": line, "reason": "error_code 缺失", "row": None})
        return
    disposition = str(row.get("disposition") or "manual").strip()
    if disposition not in _EC_DISPOSITIONS:
        c.err += 1
        c.errors.append(
            {"line": line, "reason": f"disposition 非法：{disposition}", "row": code.strip()}
        )
        return
    max_retries = _parse_int(row, ("max_retries",))
    # enabled 缺省=true（字典行默认启用）；显式给值才解析（_parse_flag 缺省 False 不适用）
    enabled = _parse_flag(row, ("enabled",)) if row.get("enabled") is not None else None
    await session.execute(
        text(
            "INSERT INTO app.listing_error_catalog"
            " (error_code, category, title, disposition, max_retries, notes, enabled)"
            " VALUES (:code, :cat, :title, :disp, COALESCE(:mr, 3), :notes,"
            "  COALESCE(:enabled, true))"
            " ON CONFLICT (error_code) DO UPDATE SET"
            "  category = EXCLUDED.category,"
            "  title = EXCLUDED.title,"
            "  disposition = EXCLUDED.disposition,"
            "  max_retries = EXCLUDED.max_retries,"
            "  notes = EXCLUDED.notes,"
            "  enabled = EXCLUDED.enabled"
        ),
        {
            "code": code.strip(),
            "cat": str(row.get("category") or "未分类").strip(),
            "title": str(row.get("title") or code).strip(),
            "disp": disposition,
            "mr": max_retries,
            "notes": (str(row["notes"]).strip() or None) if row.get("notes") else None,
            "enabled": enabled,
        },
    )
    c.ok += 1


def _parse_is_live(row: dict[str, Any]) -> bool | None:
    """显式 is_live/live_dead/live 列 → bool；无/无法识别 → None（交给字典派生）。"""
    raw = _first(row, _TM_LIVE_KEYS)
    if raw is None:
        return None
    token = raw.strip().lower()
    if token in _LIVE_TRUE:
        return True
    if token in _LIVE_FALSE:
        return False
    return None


async def _lookup_is_live(session: AsyncSession, status_code: str) -> bool | None:
    """按 status_code 查 refdata.tm_status_code 字典（查不到 → None=未知）。"""
    val = (
        await session.execute(
            text("SELECT is_live FROM refdata.tm_status_code WHERE code = :c"),
            {"c": status_code},
        )
    ).scalar_one_or_none()
    return None if val is None else bool(val)


def _parse_nice_classes(row: dict[str, Any]) -> list[int] | None:
    """nice_classes：原生 list/tuple 或逗号/空格分隔的整数串 → list[int]；否则 None。"""
    for k in _TM_NICE_KEYS:
        v = row.get(k)
        if isinstance(v, list | tuple):
            nums = [int(x) for x in v if str(x).strip().isdigit()]
            return nums or None
    raw = _first(row, _TM_NICE_KEYS)
    if raw is None:
        return None
    nums = [int(tok) for tok in raw.replace(",", " ").split() if tok.isdigit()]
    return nums or None


async def mark_failed(session: AsyncSession, *, job_id: int, error: str) -> None:
    await session.execute(
        text(
            "UPDATE app.import_job SET status = 'failed', finished_at = now(),"
            " error_report_ref = :e WHERE id = :j AND status NOT IN ('done','failed')"
        ),
        {"e": error[:500], "j": job_id},
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
