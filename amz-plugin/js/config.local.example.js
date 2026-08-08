// 本地运行配置模板（13e，③闸真机安全事件回修）。
//
// 用法：把本文件**复制**为同目录 `js/config.local.js`，填入真实值后再装载扩展。
// `js/config.local.js` 已 gitignore（永不入库）——凭证只活在这台机器上，`git diff` /
// `git add -A` / 切分支都碰不到它，根除「编辑 tracked popup.js 注入 token → 只读 diff
// 打印活值」这条已在真机兑现的泄露形状。
//
// manifest 的 content_scripts 在 popup.js 之前加载本文件；**本文件缺失时 Chrome 直接
// 拒绝加载整个扩展**（刻意的响亮失败，见联调单 E3），不是「popup.js 保留占位符 → fail-
// closed」那条路（manifest 拦在前面，真机上执行不到；popup.js 里的 typeof 兜底只服务
// node 单测沙箱）。
//
// ⚠️ baseUrl 混合内容约束：amazon 页面是 https，content script 的 http fetch 唯一豁免
// 是 loopback——浏览器须与 ERP 同机（127.0.0.1）；跨机等 RS-02b（HTTPS 反代）。
var SB2_LOCAL = {
  baseUrl: "http://127.0.0.1:8000/api/v1",
  token: "PASTE_ERP_PLUGIN_SHARED_TOKEN_HERE",
};
