# 考古B：黑名单/商标/导入触点盘点（2026-07-23）

## 1. 黑名单读路径
- L0 直查无缓存：audit/pipeline.py:80 run_l0 四表顺查（seller→asin→category→brand），_blacklist_lookup :130（status='active' AND (team_id IS NULL OR team=:t)）；类目双键 :106-113。
- L2-R4 黑名单词：pipeline.py:164 → blacklist_index.py:179 Aho-Corasick；候选 _BRANDS_SQL :130（全局+本 team active）；_MIN_LEN=4 :136,162；版本键=dataset_revision('blacklist') :120-134，_CACHE 按 team (version, Automaton) :140，懒重建 :169-176，版本全局共享。
- L2-R5 商标直查无缓存：pipeline.py:174-204（mark_norm=ANY(候选) AND is_live [AND nice && allowed]）；候选=title 大写词 :178-184。
- L3 政策块同款缓存：policy_block.py:31-94（dataset_revision('prohibited_policy')）。

## 2. 黑名单写路径
- 唯一生产写入口：compliance/import_service.py:356 _apply_row——ON CONFLICT (COALESCE(team_id,0),subject) WHERE status='active' DO NOTHING；source 硬编码 'import' :349-351。
- CLI：tools/import_blacklist.py（六域含 address/zip，normalizer BR-ORD-005 :69-82）；两段 system_tx :64-76。
- 种子 manual 10 行（0008:282）。source CHECK 四枚举 0008:44；tro_sync/trademark_sync 预留未接。
- 表级幂等：uq_{name}_active 部分唯一 0008:51；status 仅 active|removed，无 pending/候选态 0008:46。

## 3. import_job 基建
- 表 0010:37-64；domain CHECK 全集 :18-30（含 tro/phishing/suspension_case/product 未接）；RLS 按 team :68-76；权限 compliance.import_read/admin :81-83。
- 通道：create_job :140 → import_rows :195 → _process_chunks :290 分派 :244-260；源截断守卫 :224-233；块行数不符 failed 回滚 :311-321。已接域 SUPPORTED_DOMAINS :93-101。
- 端点仅只读（compliance/router.py:39,73），执行全走 CLI 无 HTTP 上传。

## 4. RS-04A bulk_import_trademark
- 用法 :14-16,316-334（csv/jsonl；--batch-size 默认 5 万 :46；--resume :320；故障注入 :168）。
- COPY UNLOGGED staging :227-232 → _MERGE_SQL set-based ON CONFLICT(serial_no) DO UPDATE :53-67 → 按批 commit :233-239；merge 行数守卫 :236-238。
- manifest sha256+逐批 done :127-166，--resume 校验 :140-153；errors.jsonl :180,215。
- revision：0014 触发器事务内 bump dataset_revision('trademark') :12。upsert 键=serial_no PK（0008:240）；归一化契约与逐行通道逐字一致 :31-43。
- 自建 import_job(domain='trademark') :194-205；同步 psycopg——周期化需 async 改造或子进程调用。
- 旧逐行通道 tools/import_trademark.py（万级）。

## 5. beat 注册模式
- TASKS 注册表 automation/tasks.py:909（16 任务，契约 async(sessions,config)->stats :41-42）。
- 调度循环 automation/beat.py:60 tick（原子领取 :97-110、config jsonb :129、timeout+run_tracked 记账告警 :130-134）。
- 样板=return_pull（tasks.py:782 → aftersale/pull.py:364）：config 零硬编码 :269-273；三段式（prepare 短 tx → HTTP 零事务 → 每页短 tx upsert）:276-326；店间失败隔离 :366-390；notify+sync_state :334-360。

## 接入点小结
1. USPTO 增量：最顺=bulk_import_trademark（COPY+merge 天然幂等+resume+revision bump）；R5 直查无缓存无需额外失效。
2. TRO 入库：无表无导入器；最顺=新增 _DOMAINS['tro'] 复用 _apply_row，但须先把 source='import' 硬编码改按域传入。
3. 黑名单候选人工确认：无 pending 状态机——候选闭环属新增设计（RS-04D 断言账本正好承载）。
