# RS-03b 验收证据（2026-07-15，沙盒 CI 级）

> 验收单：review_list RS-03.acceptance。测试文件
> `backend/tests/db/test_channel_outbox.py`（9 用例）+ 既有
> `test_listing_api.py` 8 用例零改动通过（语义保真证明）。
> 终态：**262 pytest + ruff(check+format) + mypy strict + 迁移空库演练
> （base→head→base→head 实跑）+ 前端 pnpm lint/build（含契约类型再生）全绿**。

## 验收项 → 用例对拍

| 验收项 | 用例 | 结果 |
|---|---|---|
| 同key同payload同结果 | `TestIdempotencyContract::test_outbox_same_key_same_payload_returns_existing`（outbox 层：返既有命令同 id）+ `test_api_replay_same_response_no_reexecution`（API 层：存储响应原样重放、零再发包、单 feed 行） | ✅ |
| 同key异payload 409 | `test_outbox_same_key_different_payload_409`（IDEMPOTENCY_CONFLICT + http_status=409）+ API 层 409 信封断言 | ✅ |
| 外部成功后DB回写前崩溃→verify-back不重复提交 | `TestCrashRecovery::test_crash_after_channel_success_verify_back_adopts_without_resubmit`：故障注入（POST 后响应丢失）→ 命令+feed verify_pending → 对账唯一匹配采认 → **请求序列断言 ["POST","GET"]（零重复 POST）**；命令 succeeded(via=verify_back_adopt)；期间同店车道背压实证+终局后解锁 | ✅ |
| lease/fencing 拒迟到 worker | `TestFencing::test_stale_worker_write_back_rejected`：lease 过期→sweep 归 verify_pending 且 fence+1→旧 fence 回写返 False 状态不动→对账权威通道可终局 | ✅ |
| 同store/SKU命令有序 | `TestStoreFifo::test_same_store_ordered_cross_store_independent`：同店后到命令在先到未终局时 claim 不到（pending 与 inflight 两态皆验）；异店互不挡；先到终局后放行。同店有序 ⟹ 同 SKU 有序 | ✅ |
| HTTP 期间行锁已释放 | `TestLockFreeHttp::test_no_row_locks_during_channel_call`：渠道替身处理请求当口，旁路连接对 **listing/feed/channel_command 三行 FOR UPDATE NOWAIT 全部即刻取锁**（且 tx1 产物已提交可见——feed=submitting、command=inflight 各 1 行） | ✅ |
| outbox payload 凭证/PII 脱敏 | `TestPayloadRedaction::test_forbidden_keys_rejected`（authorization/token/secret/password/credential/proxy 键族递归拒收）+ `test_real_submit_payload_contains_no_credentials`（真实命令 payload 全文扫描：client_id/secret/Bearer/access_token 零出现） | ✅ |

## 附加保真证明

- `test_listing_api.py` 全部 8 用例**零断言改动**通过（仅补契约必填的
  Idempotency-Key 头）：submit 主链/状态史全链/verify-back lost+adopt/
  dry-run 快照结构/配额与 GTIN 语义 —— 三段式改造未移动任何既有协议。
- 崩溃窗口对照：改造前「渠道已收+请求事务未提交→feed 行整体回滚=DB 失忆」；
  改造后 tx1 已提交，命令行即对账线索（考古 §1 表）。
- inbox 明确缓办（进站仅主动轮询读，无重复消费面）——随 R2-04 webhook 接入补，
  已记 specs/001 §02 channel_command 段。

## 遗留（不阻验收）

- retire 命令 verify_pending 无自动对账通道（渠道侧核对随 R2-04 维护任务；
  期间该店车道背压=有意 fail-closed）。
- api_idempotency 全表清扫（当前键内惰性清理）随 R2-04 维护任务。
- drain CLI 冒烟已跑（空队列路径）；beat 周期化随 R2-04。
