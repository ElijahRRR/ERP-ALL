// 花钱链路的纯逻辑单测（node:test，无需浏览器/无需 ERP）。
//
// 为什么这样加载：popup.js 是 content script，顶层是定义没有 export，末尾还有依赖
// 浏览器环境的初始化。故只取「CONFIG … 到 OrderProcessor 之前」这段（含本文件要测的
// 全部纯函数与 ApiService），注入最小 stub 后在隔离作用域里求值——与联调 harness 同法。
//
// 覆盖三档闸/fail-closed/token 注入的**纯逻辑**部分（SBP 的 DOM 点击闸依赖真实 DOM，
// 不在此测，由代码审查 + 真机 E4 兜；这里钉住的是能在 CI 里机器复算的那半）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const full = readFileSync(join(root, "js/popup.js"), "utf8");
const slice = full.slice(0, full.indexOf("const OrderProcessor"));
assert.ok(
  slice.includes("const ApiService"),
  "切片未含 ApiService——popup.js 结构变了，改切法",
);

function sandbox({ fetchImpl } = {}) {
  const calls = [];
  const chrome = { runtime: { getManifest: () => ({ version: "test" }) } };
  const doc = { querySelector: () => null };
  const fetch =
    fetchImpl ||
    (async (_url, opts) => {
      calls.push(opts);
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ code: 200, data: [] }),
      };
    });
  const tail = `
    globalThis.__CONFIG = CONFIG;
    globalThis.__resolveExecMode = resolveExecMode;
    globalThis.__isApiConfigured = isApiConfigured;
    globalThis.__ApiService = ApiService;
    globalThis.__stateOptionMatches = stateOptionMatches;
  `;
  const run = new Function(
    "chrome",
    "document",
    "fetch",
    "console",
    slice + tail,
  );
  run(chrome, doc, fetch, console);
  return {
    calls,
    CONFIG: globalThis.__CONFIG,
    resolveExecMode: globalThis.__resolveExecMode,
    isApiConfigured: globalThis.__isApiConfigured,
    ApiService: globalThis.__ApiService,
    stateOptionMatches: globalThis.__stateOptionMatches,
  };
}

test("resolveExecMode: 缺失/非法一律兜底 dry_run（fail-closed）", () => {
  const { resolveExecMode } = sandbox();
  assert.equal(resolveExecMode({ id: 1 }), "dry_run"); // 缺 execMode
  assert.equal(resolveExecMode({ id: 2, execMode: "prod" }), "dry_run"); // 词表外
  assert.equal(resolveExecMode({ id: 3, execMode: "" }), "dry_run"); // 空串
});

test("resolveExecMode: 三个合法档原样透传", () => {
  const { resolveExecMode } = sandbox();
  for (const m of ["live", "dry_run", "stop_before_payment"]) {
    assert.equal(resolveExecMode({ id: 1, execMode: m }), m);
  }
});

test("stateOptionMatches: 缩写比 value（含 US-/CA- 前缀），innerText 是全称也能匹配", () => {
  const { stateOptionMatches } = sandbox();
  // ③闸真机缺陷：ERP 下发 MN，Amazon option 显示 Minnesota、value=MN。
  assert.equal(stateOptionMatches("MN", "MN", "Minnesota"), true);
  assert.equal(
    stateOptionMatches("MN", "US-MN", "Minnesota"),
    true,
    "带国别前缀",
  );
  assert.equal(stateOptionMatches("ON", "CA-ON", "Ontario"), true, "加拿大省");
  assert.equal(stateOptionMatches("mn", "MN", "Minnesota"), true, "大小写无关");
  // 兜底：ERP 若下发全称、且 value 为空的异形下拉，回退比显示文字。
  assert.equal(stateOptionMatches("Minnesota", "", "Minnesota"), true);
});

test("stateOptionMatches: 不匹配的州/空值一律 false（不误选）", () => {
  const { stateOptionMatches } = sandbox();
  assert.equal(stateOptionMatches("MN", "AL", "Alabama"), false);
  assert.equal(
    stateOptionMatches("MN", "", "Minnesota"),
    false,
    "缩写≠全称文字",
  );
  assert.equal(
    stateOptionMatches("", "MN", "Minnesota"),
    false,
    "空目标不匹配",
  );
  assert.equal(stateOptionMatches("MN", "", ""), false, "占位空 option");
});

test("isApiConfigured: 占位 token / 占位 baseUrl / 空值 → false（fail-closed）", () => {
  const { CONFIG, isApiConfigured } = sandbox();
  const realBase = CONFIG.API.baseUrl;
  const realToken = CONFIG.API.token;

  CONFIG.API.token = "YOUR_PLUGIN_SHARED_TOKEN";
  assert.equal(isApiConfigured(), false, "占位 token 应 fail-closed");
  CONFIG.API.token = "";
  assert.equal(isApiConfigured(), false, "空 token 应 fail-closed");
  CONFIG.API.token = "real-token-value";

  CONFIG.API.baseUrl = "YOUR_API_BASE_URL";
  assert.equal(isApiConfigured(), false, "占位 baseUrl 应 fail-closed");
  CONFIG.API.baseUrl = realBase;

  assert.equal(isApiConfigured(), true, "真值应放行");
  CONFIG.API.token = realToken;
});

test("ApiService.request: 占位配置时一条请求都不发（fail-closed 在链路入口）", async () => {
  const { CONFIG, ApiService, calls } = sandbox();
  CONFIG.API.token = "YOUR_PLUGIN_SHARED_TOKEN";
  await assert.rejects(
    () => ApiService.request(`${CONFIG.API.baseUrl}/x`),
    /未配置|拒绝启动/,
  );
  assert.equal(calls.length, 0, "占位配置下不应发出任何 fetch");
});

test("ApiService.request: 注入 X-Plugin-Token，且调用点自带 Content-Type 不冲掉它", async () => {
  const { CONFIG, ApiService, calls } = sandbox();
  CONFIG.API.token = "real-token-value";
  await ApiService.request(
    `${CONFIG.API.baseUrl}/purchase-plugin/updateOrderStatus`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json;charset=UTF-8" }, // 回传点的真实形态
      body: "{}",
    },
  );
  assert.equal(calls.length, 1);
  const sent = calls[0].headers;
  assert.equal(
    sent["X-Plugin-Token"],
    "real-token-value",
    "自带 headers 把 token 冲掉了",
  );
  assert.equal(
    sent["Content-Type"],
    "application/json;charset=UTF-8",
    "调用点应能覆盖默认 CT",
  );
});
