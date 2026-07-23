# 考古A：RS-04D 断言账本设计输入（2026-07-23）

## 一、评审共识 B5①（specs/external-review-round-2.md）
- 分歧收敛（:15）：B5「断言分级按域启用」被接受 + 硬验收：RS-04 不得只交付空框架，须在 blacklist_brand 真实跑通。四条硬验收：①三源断言 manual+TRO+trademark 并存；②撤销一源不误删仍有效证据的品牌；③人工裁决可覆盖（压自动源）+可回滚；④canonical（有效断言投影）可全量重建且一致。
- 工单归属（:49）：RS-04D = assertion ledger + blacklist_brand 真实域验收。
- 相邻约束（:54，RS-06）：裁决带理由与 version、撤销生成新 decision 不覆盖（append-only）；(:52) 规则撤销后缓存即时失效。

## 二、001 §04 图纸（specs/001-domain-model/04-compliance.md)
- 黑名单四表骨架（:8-18）：team_id NULL=全局（超管维护）；source TEXT NOT NULL CHECK IN (import, manual, tro_sync, trademark_sync)（:16）；status active/removed 移除不删行（:17）。
- blacklist_brand（:22）：brand_norm/brand_display；唯一 (COALESCE(team_id,0), brand_norm) WHERE status='active'。
- tro_case（:30-48）：case_no/court/filed_date/plaintiff/law_firm/brand_terms JSONB（L2 检索目标）/source DEFAULT 'tro-scraper-matrix'/raw_ref/status(active,dismissed,settled)/imported_at/import_job_id；uq (case_no, COALESCE(plaintiff,''))；全局无 team_id；基线 11,893。
- refdata.trademark（:105-123）：serial_no PK/mark_norm/is_live/nice_classes SMALLINT[]；14.18M；refdata=导入管道专写业务只读。
- import_job.domain 枚举含 blacklist_brand/…/tro/trademark（:89）；compliance_hit 软标记 rule_code+list_ref（:65-81）。
- 图纸溯源落点仅单值 source 列 + import_job_id/raw_ref——无多源断言/账本/canonical/字段级优先级/人工裁决压制设计（RS-04D 缺口所在）。

## 三、现有 DDL 现状（backend/alembic/versions/）
- blacklist 四表（0008_audit_compliance.py:35-83 工厂 _blacklist_table）：列/约束/枚举与图纸一致；source DEFAULT 'manual'（图纸未定默认）；uq_{name}_active 部分唯一；RLS app schema 团队隔离，全局行仅超管可写（:23-32）；erp_app 无 DELETE（软删）。
- refdata.trademark（0008:239-256）：与图纸一致，无 RLS 全局。
- **tro_case DDL 完全缺失**（grep 0 命中）；phishing_address/zip、compliance_hit 亦未实现（spec-only）。TRO 源断言当前无上游表。
- 写路径（compliance/import_service.py:349-367）：blacklist 导入硬编码 source='import'（:350），ON CONFLICT DO NOTHING（:361-362）首写者赢不更新 source；任一写入触发 blacklist_index 版本递增失效（audit/blacklist_index.py:121）；L0 读 status='active' 投影（:131-133）。

## 设计约束清单
1. uq_brand_active 每(团队,品牌)一条 active 行——三源并存塞不进现表，source 单值列无法记多源。
2. audit L0 字典读 active 投影（blacklist_index.py:131）——canonical 必须产出等价 active 集合并保持写触发失效契约（:121）。
3. 现导入 upsert 需改为「记一条 import 源断言」而非直写 canonical。
4. 新账本沿用 app schema 四角色 RLS 与全局(NULL 超管)/团队私有语义（0008:23-32）。
5. 需新表 blacklist_assertion（一品牌 N 条源断言：并存/单源撤销/人工裁决压制/回滚），canonical 由投影重建。
6. 需建 tro_case 表（TRO 断言上游，DDL 缺失）。
7. 人工裁决 append-only：撤销生成新 decision 不覆盖（round-2:54），不在 blacklist_brand 行上原地改写。
