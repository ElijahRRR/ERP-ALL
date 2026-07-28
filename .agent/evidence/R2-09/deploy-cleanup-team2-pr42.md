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
