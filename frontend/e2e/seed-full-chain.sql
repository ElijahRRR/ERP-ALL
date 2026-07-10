-- R1-12 E2E 全链演示种子（幂等可重跑）
-- 用法：psql "postgresql://erp_migrator:erp_migrator@localhost:5432/erp_all" -f seed-full-chain.sql

INSERT INTO app.team (name) VALUES ('E2E演示团队') ON CONFLICT (name) DO NOTHING;

DO $$
DECLARE
  tid bigint;
  uid bigint;
  rid bigint;
  cid bigint;
  sid bigint;
BEGIN
  SELECT id INTO tid FROM app.team WHERE name = 'E2E演示团队';

  -- 清理旧演示数据
  DELETE FROM app.notification_target WHERE notification_id IN
    (SELECT id FROM app.notification WHERE team_id = tid);
  DELETE FROM app.notification WHERE team_id = tid;
  DELETE FROM app.listing_state_history WHERE team_id = tid;
  DELETE FROM app.feed_item WHERE team_id = tid;
  DELETE FROM app.feed WHERE team_id = tid;
  DELETE FROM app.listing_spec WHERE team_id = tid;
  DELETE FROM app.listing WHERE team_id = tid;
  DELETE FROM app.gtin_pool WHERE team_id = tid;
  DELETE FROM app.audit_hit WHERE team_id = tid;
  DELETE FROM app.audit_run WHERE team_id = tid;
  DELETE FROM app.scrape_result WHERE team_id = tid;
  DELETE FROM app.scrape_task WHERE team_id = tid;
  DELETE FROM app.scrape_job WHERE team_id = tid;
  DELETE FROM app.product WHERE team_id = tid;
  DELETE FROM app.quota_usage WHERE store_id IN (SELECT id FROM app.store WHERE team_id = tid);
  DELETE FROM app.quota_config WHERE store_id IN (SELECT id FROM app.store WHERE team_id = tid);
  DELETE FROM app.store_credential WHERE store_id IN (SELECT id FROM app.store WHERE team_id = tid);
  DELETE FROM app.store WHERE team_id = tid;
  UPDATE app.scrape_task SET worker_id = NULL WHERE worker_id IN
    (SELECT id FROM app.worker_node WHERE node_key LIKE 'e2e-%');
  DELETE FROM app.worker_node WHERE node_key LIKE 'e2e-%';
  DELETE FROM app.llm_cache WHERE true;

  -- 演示用户（密码=super-secret-pass-1；hash 为应用 argon2id 预生成常量）
  DELETE FROM app.user_role WHERE user_id IN
    (SELECT id FROM app.app_user WHERE username = 'e2e_demo');
  DELETE FROM app.app_user WHERE username = 'e2e_demo';
  INSERT INTO app.app_user (team_id, username, password_hash, display_name)
  VALUES (tid, 'e2e_demo',
    '$argon2id$v=19$m=65536,t=3,p=4$U1kmu+wE6ACEX5huEC2nqQ$uWOFKRawCIpDHJ5+bZra+IDqAZ+F6pbl9RlypZk3e4A',
    'E2E演示员') RETURNING id INTO uid;
  DELETE FROM app.role WHERE team_id = tid AND name = 'E2E演示角色';
  INSERT INTO app.role (team_id, name) VALUES (tid, 'E2E演示角色') RETURNING id INTO rid;
  INSERT INTO app.role_permission (role_id, permission_code)
  SELECT rid, c FROM unnest(ARRAY[
    'scrape.job_read','scrape.job_write','catalog.product_read',
    'audit.run','audit.read',
    'listing.read','listing.allocate','listing.submit','listing.delist',
    'catalog.gtin_read','catalog.import_write','channel.store_read'
  ]) AS c;
  INSERT INTO app.user_role (user_id, role_id) VALUES (uid, rid);

  -- 测试店 + 凭证（dev credential_key）
  SELECT id INTO cid FROM app.channel WHERE code = 'walmart_us';
  INSERT INTO app.store (team_id, channel_id, code, name, is_test)
  VALUES (tid, cid, 'A152E', 'E2E演示店', true) RETURNING id INTO sid;
  INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)
  VALUES (sid, 'e2e-client-01', pgp_sym_encrypt('e2e-secret-0001', 'dev-only-change-me'));
  -- 配额：listing_create 每日 2 条（演示失败路径②——第 3 次提交被拒）
  INSERT INTO app.quota_config (store_id, quota_kind, daily_limit)
  VALUES (sid, 'listing_create', 2);

  -- 系统配置
  INSERT INTO app.system_config (key, value) VALUES
    ('channel.gateway_mode', '"live_test"'),
    ('listing.default_wpt', '"Drinkware"'),
    ('scrape.worker_enroll_token', '"e2e-enroll-token"'),
    ('llm.pricing', '{"deepseek-chat": {"input_per_1m": 0.27, "output_per_1m": 1.1}}')
  ON CONFLICT (key) DO UPDATE SET value = excluded.value;
END $$;
