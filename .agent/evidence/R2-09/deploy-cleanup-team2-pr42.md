# team 2（R2-02对拍 残留）删除指令（给部署 AI，可整段粘贴）

> **Owner 2026-07-28 裁定 (c)**：删除 team 2 后重验 PR #42 第三闸。
> 定性依据（诊断回执 DIAG_EXIT=0）：名即「R2-02对拍」、audit_log 无其建团行、
> 模板 7 角色全缺、2026-07-14 直插建团特征——验证残留，非真团队。
>
> **设计**：单事务 + 双硬闸。身份核对不过 → 中止；team 2 名下存在**任何**超出
> 「1 个用户 + 其角色绑定」的行（28 张 FK 表逐一清点）→ 中止。任一中止 = 整体回滚，
> 库分毫不动。审计三族（audit_log/audit_hit/audit_run）无 team FK，**一行不删**——留痕。

## 铁律

1. 只跑本文这一段，**不要自行扩大删除范围**；报错就停下贴回，不要改了再试。
2. 不输出密钥；机器保持 `main`，本指令不切分支。
3. `RETURNING` 的行是删除取证，**原样贴回**。

## 执行（单事务：任何一步异常则全部回滚）

```powershell
$sql = @'
\pset pager off
-- 闸 1：身份核对——id=2 必须叫「R2-02对拍」，否则中止
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM app.team WHERE id = 2 AND name = 'R2-02对拍') THEN
    RAISE EXCEPTION 'team 2 身份核对失败（不是 R2-02对拍），中止';
  END IF;
END $$;

-- 闸 2：28 张 FK 表逐一清点——除 app_user 恰 1 行外必须全 0，否则中止
-- （防的是「残留团队名下还挂着业务数据」——那超出本次授权，须回报 Owner 另裁）
DO $$
DECLARE r record; n bigint; bad text := '';
BEGIN
  FOR r IN
    SELECT DISTINCT c.conrelid::regclass::text AS tbl, a.attname AS col
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
    WHERE c.confrelid = 'app.team'::regclass AND c.contype = 'f'
  LOOP
    EXECUTE format('SELECT count(*) FROM %s WHERE %I = 2', r.tbl, r.col) INTO n;
    IF r.tbl = 'app.app_user' AND r.col = 'team_id' THEN
      IF n <> 1 THEN bad := bad || format(' [%s=%s 期望1]', r.tbl, n); END IF;
    ELSIF n <> 0 THEN
      bad := bad || format(' [%s.%s=%s]', r.tbl, r.col, n);
    END IF;
  END LOOP;
  IF bad <> '' THEN
    RAISE EXCEPTION 'team 2 名下存在超出授权范围的行，中止:%', bad;
  END IF;
END $$;

-- 三删，顺序固定；RETURNING 即取证
-- （user_role 的 role_id 顺带回答了「team 2 用户绑的到底是谁的角色」）
DELETE FROM app.user_role
 WHERE user_id IN (SELECT id FROM app.app_user WHERE team_id = 2)
 RETURNING user_id, role_id;
DELETE FROM app.app_user WHERE team_id = 2 RETURNING id, username, is_super;
DELETE FROM app.team WHERE id = 2 RETURNING id, name;

-- 终检
SELECT count(*) AS remaining_teams FROM app.team;
'@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -1 -U postgres -d erp_all
echo "CLEANUP_TEAM2_EXIT=$LASTEXITCODE"
```

**贴回**：全部输出 + `CLEANUP_TEAM2_EXIT`。

**判据**：
- `CLEANUP_TEAM2_EXIT=0`，三段 `DELETE ... RETURNING` 各有行（绑定 ≥1 / 用户 1 / 团队 1），
  `remaining_teams = 1`；
- 若报 `中止`：**什么都没删**（单事务已回滚），把异常信息整段贴回——特别是闸 2 列出的
  超范围行，那需要 Owner 追加裁定。

## 删除成功后：重验 PR #42 第三闸

**直接重跑 `.agent/evidence/R2-09/deploy-verify-pr42.md` 全文（前置→⑫）**，判据一字不改：
删除后本机 1 个团队，C 段应「(模板) + team 1」共 2 行、全为 2，E 段 total_teams=1，
行数关系「团队数 + 1」自然成立。UI 四项（第 4 步）用 team 1 的非超管团管账号做。

---

# v2（Owner 2026-07-28 扩权后）：连 400 行对拍产品一起删净

> v1 闸 2 按设计中止：`app.product.team_id=2` 有 **400 行**（R2-02 对拍导入的测试产品）。
> Owner 追加授权：**扩权删净，含 400 行产品**。
>
> v2 变化：闸 2 的例外集从 {app_user=1} 扩为 {app_user=1, **product=恰 400**}（行数变了
> 说明机上状态与授权时不同，中止）；新增**闸 3**：product 的三张引用表
> （variant_member / listing / listing_spec）对 team 2 产品必须 0 行——非 0 即中止回滚
> （那说明「裸产品行」的定性不成立，须再回报）。删除顺序：产品 → 绑定 → 用户 → 团队。
> 仍单事务，任一异常整体回滚。铁律同 v1。

```powershell
$sql = @'
\pset pager off
-- 闸 1：身份核对
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM app.team WHERE id = 2 AND name = 'R2-02对拍') THEN
    RAISE EXCEPTION 'team 2 身份核对失败（不是 R2-02对拍），中止';
  END IF;
END $$;

-- 闸 2：28 张 team FK 表清点——例外仅 app_user=1、product=400，其余非 0 即中止
DO $$
DECLARE r record; n bigint; bad text := '';
BEGIN
  FOR r IN
    SELECT DISTINCT c.conrelid::regclass::text AS tbl, a.attname AS col
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
    WHERE c.confrelid = 'app.team'::regclass AND c.contype = 'f'
  LOOP
    EXECUTE format('SELECT count(*) FROM %s WHERE %I = 2', r.tbl, r.col) INTO n;
    IF r.tbl = 'app.app_user' AND r.col = 'team_id' THEN
      IF n <> 1 THEN bad := bad || format(' [%s=%s 期望1]', r.tbl, n); END IF;
    ELSIF r.tbl = 'app.product' AND r.col = 'team_id' THEN
      IF n <> 400 THEN bad := bad || format(' [%s=%s 期望400，与授权时不符]', r.tbl, n); END IF;
    ELSIF n <> 0 THEN
      bad := bad || format(' [%s.%s=%s]', r.tbl, r.col, n);
    END IF;
  END LOOP;
  IF bad <> '' THEN
    RAISE EXCEPTION 'team 2 清点与授权范围不符，中止:%', bad;
  END IF;
END $$;

-- 闸 3：三张产品引用表对 team 2 产品必须 0 行（「裸产品行」定性的机器复核）
DO $$
DECLARE n1 bigint; n2 bigint; n3 bigint;
BEGIN
  SELECT count(*) INTO n1 FROM app.variant_member
   WHERE product_id IN (SELECT id FROM app.product WHERE team_id = 2);
  SELECT count(*) INTO n2 FROM app.listing
   WHERE product_id IN (SELECT id FROM app.product WHERE team_id = 2);
  SELECT count(*) INTO n3 FROM app.listing_spec
   WHERE product_id IN (SELECT id FROM app.product WHERE team_id = 2);
  IF n1 <> 0 OR n2 <> 0 OR n3 <> 0 THEN
    RAISE EXCEPTION '产品有下游挂载（variant_member=% listing=% listing_spec=%），中止', n1, n2, n3;
  END IF;
END $$;

-- 四删，顺序固定；计数即取证
WITH del AS (DELETE FROM app.product WHERE team_id = 2 RETURNING id)
SELECT count(*) AS deleted_products FROM del;
DELETE FROM app.user_role
 WHERE user_id IN (SELECT id FROM app.app_user WHERE team_id = 2)
 RETURNING user_id, role_id;
DELETE FROM app.app_user WHERE team_id = 2 RETURNING id, username, is_super;
DELETE FROM app.team WHERE id = 2 RETURNING id, name;

-- 终检
SELECT count(*) AS remaining_teams FROM app.team;
SELECT count(*) AS remaining_t2_products FROM app.product WHERE team_id = 2;
'@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -1 -U postgres -d erp_all
echo "CLEANUP_TEAM2_V2_EXIT=$LASTEXITCODE"
```

**贴回**：全部输出 + `CLEANUP_TEAM2_V2_EXIT`。

**判据**：`CLEANUP_TEAM2_V2_EXIT=0`；`deleted_products = 400`；三段 DELETE RETURNING
（绑定 ≥1 / 用户 1 / 团队 1）；`remaining_teams = 1`、`remaining_t2_products = 0`。
若报「中止」= 分毫未动，异常整段贴回。

**删除成功后**：直接重跑 `.agent/evidence/R2-09/deploy-verify-pr42.md` 全文（前置→⑫），
判据一字不改。

---

# v3 终版（Owner 2026-07-28 全量清点后终裁）：产品 400 + 规格产物 4 + 流水 1580 + 用户/绑定/团队

> 全量清点（FULL_CENSUS_EXIT=0）后 Owner 终裁：4 行 listing_spec（删产品前置依赖，已预览）
> 与 1580 行 llm_usage_log（费用流水）**一并删**；审计三族（audit_run 2400 / audit_hit 6098 /
> audit_log 0）**铁律保留**，孤儿化无害。除审计外 team 2 彻底无痕。
>
> 闸设计：每一项行数**钉死在清点值**（400/4/1580/1）——任何一项与授权时不符即中止整体回滚。
> 仍单事务。铁律同 v1（只跑本段、不扩范围、报错即停、不输出密钥、机器保持 main）。

```powershell
$sql = @'
\pset pager off
-- 闸 1：身份核对
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM app.team WHERE id = 2 AND name = 'R2-02对拍') THEN
    RAISE EXCEPTION 'team 2 身份核对失败（不是 R2-02对拍），中止';
  END IF;
END $$;

-- 闸 2：28 张 team FK 表清点——例外仅 app_user=1、product=400，其余非 0 即中止
DO $$
DECLARE r record; n bigint; bad text := '';
BEGIN
  FOR r IN
    SELECT DISTINCT c.conrelid::regclass::text AS tbl, a.attname AS col
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
    WHERE c.confrelid = 'app.team'::regclass AND c.contype = 'f'
  LOOP
    EXECUTE format('SELECT count(*) FROM %s WHERE %I = 2', r.tbl, r.col) INTO n;
    IF r.tbl = 'app.app_user' AND r.col = 'team_id' THEN
      IF n <> 1 THEN bad := bad || format(' [%s=%s 期望1]', r.tbl, n); END IF;
    ELSIF r.tbl = 'app.product' AND r.col = 'team_id' THEN
      IF n <> 400 THEN bad := bad || format(' [%s=%s 期望400]', r.tbl, n); END IF;
    ELSIF n <> 0 THEN
      bad := bad || format(' [%s.%s=%s]', r.tbl, r.col, n);
    END IF;
  END LOOP;
  IF bad <> '' THEN
    RAISE EXCEPTION 'team 2 清点与授权范围不符，中止:%', bad;
  END IF;
END $$;

-- 闸 3：产品引用表——variant_member=0、listing=0、listing_spec=恰 4，否则中止
DO $$
DECLARE n1 bigint; n2 bigint; n3 bigint;
BEGIN
  SELECT count(*) INTO n1 FROM app.variant_member
   WHERE product_id IN (SELECT id FROM app.product WHERE team_id = 2);
  SELECT count(*) INTO n2 FROM app.listing
   WHERE product_id IN (SELECT id FROM app.product WHERE team_id = 2);
  SELECT count(*) INTO n3 FROM app.listing_spec
   WHERE product_id IN (SELECT id FROM app.product WHERE team_id = 2);
  IF n1 <> 0 OR n2 <> 0 OR n3 <> 4 THEN
    RAISE EXCEPTION '挂载与授权不符（variant_member=% listing=% listing_spec=%，期望 0/0/4），中止', n1, n2, n3;
  END IF;
END $$;

-- 闸 4：流水行数钉死——llm_usage_log 恰 1580，否则中止
DO $$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n FROM app.llm_usage_log WHERE team_id = 2;
  IF n <> 1580 THEN
    RAISE EXCEPTION 'llm_usage_log=% 与授权时 1580 不符，中止', n;
  END IF;
END $$;

-- 六删，顺序固定；计数即取证
WITH del AS (
  DELETE FROM app.listing_spec
   WHERE product_id IN (SELECT id FROM app.product WHERE team_id = 2) RETURNING id)
SELECT count(*) AS deleted_listing_specs FROM del;
WITH del AS (DELETE FROM app.product WHERE team_id = 2 RETURNING id)
SELECT count(*) AS deleted_products FROM del;
WITH del AS (DELETE FROM app.llm_usage_log WHERE team_id = 2 RETURNING id)
SELECT count(*) AS deleted_llm_usage FROM del;
DELETE FROM app.user_role
 WHERE user_id IN (SELECT id FROM app.app_user WHERE team_id = 2)
 RETURNING user_id, role_id;
DELETE FROM app.app_user WHERE team_id = 2 RETURNING id, username, is_super;
DELETE FROM app.team WHERE id = 2 RETURNING id, name;

-- 终检：删净 + 审计三族纹丝不动
SELECT count(*) AS remaining_teams FROM app.team;
SELECT count(*) AS t2_products FROM app.product WHERE team_id = 2;
SELECT count(*) AS t2_specs FROM app.listing_spec WHERE team_id = 2;
SELECT count(*) AS t2_llm FROM app.llm_usage_log WHERE team_id = 2;
SELECT 'audit_run' AS tbl, count(*) AS rows FROM app.audit_run WHERE team_id = 2
UNION ALL SELECT 'audit_hit', count(*) FROM app.audit_hit WHERE team_id = 2;
'@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -1 -U postgres -d erp_all
echo "CLEANUP_TEAM2_V3_EXIT=$LASTEXITCODE"
```

**贴回**：全部输出 + `CLEANUP_TEAM2_V3_EXIT`。

**判据**：`CLEANUP_TEAM2_V3_EXIT=0`；`deleted_listing_specs=4`、`deleted_products=400`、
`deleted_llm_usage=1580`；三段 DELETE RETURNING（绑定 ≥1 / 用户 1 / 团队 1）；
终检 `remaining_teams=1`、`t2_products=0`、`t2_specs=0`、`t2_llm=0`、
审计两行仍为 `2400` / `6098`（**纹丝不动**——变了即报）。
若报「中止」= 分毫未动，异常整段贴回。

**删除成功后**：直接重跑 `.agent/evidence/R2-09/deploy-verify-pr42.md` 全文（前置→⑫），
判据一字不改。
