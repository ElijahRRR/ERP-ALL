# 本机智能体执行指令（数据调研任务）

> 你是在 Owner 本机（macOS，项目根 `~/Projects/erpAPI`）执行数据调研的 agent。
> 背景：erpAPI 正在做从零重建的新 ERP（R0 需求阶段），远端 agent 已产出调研清单，
> 需要你在本机采集数据资产的**结构/规模/样例**并推回 GitHub。大数据本身不上传。

## 前置

```bash
cd ~/Projects/erpAPI
git fetch origin claude/project-deep-review-8yls6u
git checkout claude/project-deep-review-8yls6u
git pull origin claude/project-deep-review-8yls6u
```

先通读 `erp-core/specs/011-r0-requirements/data-survey/README.md`（调研范围的唯一依据）。

## 硬约束（违反即停）

1. **对所有数据源只读**：只允许 SELECT / `.schema` / `pg_dump --schema-only` / lark 读接口；
   禁止任何 UPDATE/DELETE/写飞书/调 Walmart API。
2. **不提交大文件**：单文件 >1MB 不许 commit；样例一律 LIMIT 10~20。
3. **隐私检查**：commit 前逐个查看 `out/` 样例文件，若出现客户姓名/地址/邮箱/电话，删掉该列或打码。
4. 不修改本分支已有的任何文档/代码，只往 `erp-core/specs/011-r0-requirements/data-survey/out/` 和
   `ANSWERS.md` 新增内容。

## 任务

### T1 跑采集脚本（PG + SQLite）

```bash
bash erp-core/specs/011-r0-requirements/data-survey/survey_local_data.sh
```

- 脚本内 `TRO_DB` 路径若不对，先 `ls ~/Projects | grep -i tro` 找到 tro-scraper-matrix 的 merged.db 改后重跑该项。
- 某表名/库名对不上时，看对应 `*_schema.sql` 输出后手工补一条等价查询，不要跳过。
- 全部输出落 `data-survey/out/`。

### T2 导出 8 张飞书表（表头 + 前 10 行 + 总行数 → CSV 到 out/lark/）

按 README §三 的清单执行。工具任选其一：
- `lark-cli`（用法见项目根 `docs/lark-cli-reference.md`，统一 `--as bot`）；
- 或项目内 `lark_io` 模块（有 989 行测试，API 见 `lark_io/__init__.py`）。

注意：定价表（X4vMwQ…/2FJ2Np）要导**前 3 行**（双行表头）；UPC 池只要总行数 + B 列状态分布，不导全量。
行数用 workbook-info / sheet 元信息拿，不要全表拉取 148k 行的大表。

### T3 回答 4 个「位置未知」问题 → 写 `data-survey/ANSWERS.md`

在本机搜索并回答（每题给出：存放位置、表头/字段列表、大致行数、10 行内样例路径或内嵌）：
1. **黑名单卖家**数据在哪维护？（搜索建议：飞书各 workbook 的 sheet 列表、`~/Downloads`、其他项目目录、grep "卖家" 相关脚本）
2. **店铺收款账户资料**存在哪？字段有哪些（数值可打码）？
3. **1688 货源对应记录**（产品↔链接/价格）是否存在？（搜 Excel/飞书/备忘）
4. **代理 IP 台账**（采购/到期/续费信息）是否存在于店铺API.xlsx 之外？
找不到的就如实写"未找到，Owner 需口头确认"。

### T4（顺手，独立提交）类目映射入库（Owner 已批准，决策 D-Q21）

```bash
git checkout main && git pull origin main
# 编辑 .gitignore：删除整目录忽略行「类目映射/」（保留 类目映射/data/*.json 和 *.xlsx 两行大文件忽略）
git add .gitignore 类目映射/
git commit -m "chore: 类目映射模块入库（解除整目录 gitignore，保留大数据文件忽略）"
git push origin main
git checkout claude/project-deep-review-8yls6u
```
若 `git add 类目映射/` 出现 >50MB 文件，先把该文件补进 .gitignore 再加。

### T5 提交

```bash
git add erp-core/specs/011-r0-requirements/data-survey/
git commit -m "data(specs): 本地数据调研产物 — PG/SQLite/飞书 结构规模样例 + 位置未知资产答复"
git push origin claude/project-deep-review-8yls6u
```

## 完成标准

- `out/` 内有 §1/§2 的 schema + rowcounts + 样例文件，`out/lark/` 内有 8 张表的 CSV；
- `ANSWERS.md` 四题都有明确答复（含"未找到"）；
- 类目映射已入 main；
- 无 >1MB 文件、无未打码个人信息；
- push 成功后向 Owner 汇报：产物清单 + 遇到的路径/权限问题。
