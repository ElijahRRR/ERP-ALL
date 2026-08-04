const CONFIG = {
  API: {
    // ⚠️ **凭证绝不写进本 tracked 文件**：③闸真机实测——把 token 注入 tracked 的
    // popup.js 后，只读 `git diff` 就把活值打印到终端（凭证泄露已兑现）。故 token 与
    // baseUrl 改从 `js/config.local.js`（gitignore、不入库）里的 `SB2_LOCAL` 注入；
    // 该文件缺失时保留占位符 → isApiConfigured() fail-closed（一条请求都不发）。
    // 装载前：复制 js/config.local.example.js 为 js/config.local.js 并填真值。
    // baseUrl 混合内容约束：amazon 页面是 https，content script 的 http fetch 唯一豁免
    // 是 loopback——浏览器须与 ERP 同机（127.0.0.1）；跨机等 RS-02b（HTTPS 反代）。
    baseUrl:
      (typeof SB2_LOCAL !== "undefined" && SB2_LOCAL.baseUrl) ||
      "http://127.0.0.1:8000/api/v1",
    token:
      (typeof SB2_LOCAL !== "undefined" && SB2_LOCAL.token) ||
      "YOUR_PLUGIN_SHARED_TOKEN",
    endpoints: {
      getNeedSyncOrders: "/purchase-plugin/getNeedSyncOrders",
      // Amz To Jc链：保留厂商原路径逐字不动（打到ERP即404 fail-closed，不扩工单范围）
      getNeedSyncOrdersTrack: "/system/amazonOrderPig/getNeedSyncOrders",
      updateOrderTracking: "/purchase-plugin/updateTrackingInfo",
      // Amz To Jc链：保留厂商原路径逐字不动（打到ERP即404 fail-closed，不扩工单范围）
      updateOrderTrackingInfo: "/system/amazonOrderPig/updateTrackingInfo",
      updateAmzOrderStatus: "/purchase-plugin/updateAmzOrderStatus",
      getPurchaseOrders: "/purchase-plugin/getNeedPurchaseOrders",
      purchaseOrderFinishUpdate: "/purchase-plugin/purchaseOrderFinishUpdate",
      updateOrderStatus: "/purchase-plugin/updateOrderStatus",
    },
  },
  PAGES: {
    amazonOrderPages: [
      "amazon.com/your-orders/orders",
      "amazon.com/gp/css/order-history",
      "amazon.com/gp/your-account/order-history",
      "amazon.ca/your-orders/orders",
      "amazon.ca/gp/css/order-history",
      "amazon.ca/gp/your-account/order-history",
      "amazon.co.jp/your-orders/orders",
      "amazon.co.jp/gp/css/order-history",
      "amazon.co.jp/-/en/gp/css/order-history?",
      "amazon.co.jp/gp/your-account/order-history",
    ],
  },
  SITES: {
    US: {
      domain: "amazon.com",
      name: "美国",
      purchaseSupported: true,
      timezone: "America/Los_Angeles",
      timezoneName: "美西时区",
    },
    CA: {
      domain: "amazon.ca",
      name: "加拿大",
      purchaseSupported: true,
      timezone: "America/Toronto",
      timezoneName: "加拿大东部时区",
    },
    JP: {
      domain: "amazon.co.jp",
      name: "日本",
      purchaseSupported: true,
      timezone: "Asia/Tokyo",
      timezoneName: "日本时区",
    },
  },
  UI: {
    maxLogs: 20,
    layerConfig: { area: ["1000px", "700px"], shade: 0.8, closeBtn: 1 },
  },
  TIME: { retryDelay: 1e3, maxRetries: 3, waitTimeout: 3e4 },
};
// fail-closed：baseUrl/token 仍是占位串或为空时，统一请求器拦下，一条请求都不发
function isApiConfigured() {
  const baseUrl = CONFIG.API.baseUrl;
  const token = CONFIG.API.token;
  if (!baseUrl || baseUrl === "YOUR_API_BASE_URL") {
    return false;
  }
  if (!token || token === "YOUR_PLUGIN_SHARED_TOKEN") {
    return false;
  }
  return true;
}
// 拍单三档：任务对象缺 execMode 或值非法一律按 dry_run 兜底（fail-closed）
const VALID_EXEC_MODES = ["live", "dry_run", "stop_before_payment"];
function resolveExecMode(order) {
  if (order._resolvedExecMode) {
    return order._resolvedExecMode;
  }
  let mode = order.execMode;
  if (!VALID_EXEC_MODES.includes(mode)) {
    console.warn(
      `订单 ${order.orderNo || order.id} execMode 缺失或非法(${mode})，按 dry_run 兜底`,
    );
    logToOutput(
      `订单 execMode 缺失或非法(${mode})，按 dry_run 处理（fail-closed）`,
      "error",
    );
    mode = "dry_run";
  }
  order._resolvedExecMode = mode;
  return mode;
}
// 州/省匹配（③闸真机缺陷修复）：Amazon 地址下拉的 option **value 是缩写**
// （USPS/省缩写，可能带国别前缀如 US-MN、CA-ON），innerText 是全称（Minnesota）。
// ERP 按契约下发的是缩写（MN），旧实现只比 innerText → 合法缩写必然落入失败分支。
// 改为**先比规范化的 value**（去国别前缀、大写），innerText 仅作兜底（ERP 若改发全称、
// 或 value 为空的异形下拉时才用）。抽成纯函数以便 CI 单测（无需真实 DOM）。
function stateOptionMatches(stateToMatch, optionValue, optionText) {
  const want = String(stateToMatch || "")
    .trim()
    .toUpperCase();
  if (!want) return false;
  const valNorm = String(optionValue || "")
    .trim()
    .toUpperCase()
    .split("-")
    .pop();
  if (valNorm && valNorm === want) return true;
  const textNorm = String(optionText || "")
    .trim()
    .toUpperCase();
  return textNorm !== "" && textNorm === want;
}
// 阶段化诊断日志（③闸「加载慢」待诊断项）：记录相对拍单起点的耗时 + document.readyState
// + 是否命中挑战/验证页，**不记录任何页面内容**（避免敏感信息进日志）。下一次真机可据此
// 直接定位卡点（导航竞态 / DOM ready 判据 / 挑战页 / 超时策略）。
let _sb2PurchaseStartTs = 0;
function markPurchaseStart() {
  _sb2PurchaseStartTs = Date.now();
}
function isChallengePage(doc) {
  try {
    const d = doc || document;
    const html = (d.body && d.body.innerText ? d.body.innerText : "").slice(
      0,
      4000,
    );
    return /captcha|verify|robot|puzzle|拼图|验证|机器人/i.test(html);
  } catch (e) {
    return false;
  }
}
function logStage(name, doc) {
  const dt = _sb2PurchaseStartTs ? Date.now() - _sb2PurchaseStartTs : 0;
  const d = doc || document;
  const rs = d && d.readyState ? d.readyState : "n/a";
  const ch = isChallengePage(d) ? " challenge=1" : "";
  logToOutput(`[阶段 ${name}] +${dt}ms readyState=${rs}${ch}`);
}
function isAmazonOrderPage() {
  return CONFIG.PAGES.amazonOrderPages.some((page) =>
    window.location.href.includes(page),
  );
}
function detectAmazonSite() {
  const hostname = window.location.hostname;
  if (hostname.includes("amazon.com")) {
    return CONFIG.SITES.US;
  } else if (hostname.includes("amazon.ca")) {
    return CONFIG.SITES.CA;
  } else if (hostname.includes("amazon.co.jp")) {
    return CONFIG.SITES.JP;
  }
  return null;
}
function isPurchaseSupported() {
  const site = detectAmazonSite();
  return site ? site.purchaseSupported : false;
}
function logToOutput(message, type = "info") {
  const logContent = document.querySelector(".sb2-log-content");
  if (logContent) {
    const logEntry = document.createElement("div");
    logEntry.className = "sb2-log-entry";
    if (type === "error") {
      logEntry.classList.add("error");
    }
    logEntry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    logContent.appendChild(logEntry);
    const maxLogs = CONFIG.UI.maxLogs;
    const logs = logContent.querySelectorAll(".sb2-log-entry");
    if (logs.length > maxLogs) {
      for (let i = 0; i < logs.length - maxLogs; i++) {
        logContent.removeChild(logs[i]);
      }
    }
    logContent.scrollTop = logContent.scrollHeight;
  }
}
function extractCustomerId() {
  const pageContent = document.documentElement.innerHTML;
  const regex1 = /customerId:\s*"([^"]*)"/;
  const match1 = pageContent.match(regex1);
  if (match1 && match1[1]) {
    logToOutput(`成功提取 customerId: ${match1[1]}`);
    return match1[1];
  }
  const regex2 = /customerId:\s*'([^']*)'/;
  const match2 = pageContent.match(regex2);
  if (match2 && match2[1]) {
    logToOutput(`成功提取 customerId: ${match2[1]}`);
    return match2[1];
  }
  logToOutput("未找到 customerId，两种格式都尝试失败", "error");
  return null;
}
const Utils = {
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  async retry(
    requestFn,
    maxRetries = CONFIG.TIME.maxRetries,
    delay = CONFIG.TIME.retryDelay,
  ) {
    let lastError;
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        return await requestFn();
      } catch (error) {
        lastError = error;
        if (attempt < maxRetries) {
          logToOutput(
            `请求失败，第 ${attempt} 次重试... (${error.message})`,
            "error",
          );
          await this.sleep(delay);
          delay *= 2;
        }
      }
    }
    throw lastError;
  },
  clearLogOutput() {
    const logContent = document.querySelector(".sb2-log-content");
    if (logContent) {
      logContent.innerHTML = "";
    }
  },
  getDeliveryDaysLimit() {
    const savedDays = localStorage.getItem("sb2-delivery-days-limit");
    return savedDays ? parseInt(savedDays) : 7;
  },
  compressHTML(html) {
    return html.replace(/\s+/g, " ").trim();
  },
  getCompleteHTML(windowObj) {
    const html = windowObj.document.documentElement.outerHTML;
    return this.compressHTML(html);
  },
  extractUrlParams(url) {
    const params = {};
    const urlObj = new URL(url);
    for (const [key, value] of urlObj.searchParams) {
      params[key] = value;
    }
    return params;
  },
  async waitForPageNavigation(
    iframe,
    expectedUrlPatterns = [],
    maxWaitTime = 3e4,
    checkInterval = 500,
  ) {
    return new Promise((resolve) => {
      let startTime = Date.now();
      let lastUrl = "";
      let stableCount = 0;
      const requiredStableCount = 3;
      const checkNavigation = () => {
        try {
          const currentUrl = iframe.contentWindow.location.href;
          const currentTime = Date.now();
          if (currentTime - startTime > maxWaitTime) {
            console.log(`页面跳转等待超时，当前URL: ${currentUrl}`);
            resolve({ success: false, url: currentUrl, reason: "timeout" });
            return;
          }
          if (currentUrl !== lastUrl) {
            console.log(`检测到URL变化: ${lastUrl} -> ${currentUrl}`);
            lastUrl = currentUrl;
            stableCount = 0;
          } else {
            stableCount++;
          }
          if (stableCount >= requiredStableCount) {
            if (expectedUrlPatterns.length > 0) {
              const matchesPattern = expectedUrlPatterns.some((pattern) => {
                if (typeof pattern === "string") {
                  return currentUrl.includes(pattern);
                } else if (pattern instanceof RegExp) {
                  return pattern.test(currentUrl);
                }
                return false;
              });
              if (matchesPattern) {
                console.log(`页面跳转完成，匹配预期模式: ${currentUrl}`);
                resolve({
                  success: true,
                  url: currentUrl,
                  reason: "pattern_matched",
                });
                return;
              }
            } else {
              console.log(`页面跳转完成，URL稳定: ${currentUrl}`);
              resolve({ success: true, url: currentUrl, reason: "url_stable" });
              return;
            }
          }
          setTimeout(checkNavigation, checkInterval);
        } catch (error) {
          console.log(`检查页面跳转时出错: ${error.message}`);
          setTimeout(checkNavigation, checkInterval);
        }
      };
      checkNavigation();
    });
  },
};
const ApiService = {
  async request(url, options = {}) {
    // fail-closed：baseUrl/token 仍是占位串或为空，链路入口直接拦下，不发出这条请求
    if (!isApiConfigured()) {
      const msg = `ERP API 未配置（baseUrl/token 仍为占位符），拒绝启动，未发送任何请求: ${url}`;
      console.error(msg);
      logToOutput(msg, "error");
      throw new Error(msg);
    }
    // headers 单独摘出后置合并：若把 ...options 展开在 headers 之后，调用点自带的
    // headers 会整体覆盖合并结果，X-Plugin-Token 被静默冲掉（回传点恰好带自定义
    // Content-Type，等于所有写回全 401）——顺序就是这条闸本身
    const { headers: extraHeaders, ...restOptions } = options;
    const response = await fetch(url, {
      ...restOptions,
      headers: {
        "Content-Type": "application/json",
        "X-Plugin-Token": CONFIG.API.token,
        ...extraHeaders,
      },
    });
    // 响应体只能读一次，先取原文，成功/失败都要能把响应体原文写入日志
    const rawText = await response.text();
    let body = null;
    try {
      body = rawText ? JSON.parse(rawText) : null;
    } catch (e) {
      body = null;
    }
    if (!response.ok) {
      // ERP失败走真实状态码 + ErrorResponse JSON，原文写日志
      logToOutput(
        `服务器返回错误: HTTP ${response.status} - ${rawText}`,
        "error",
      );
      throw new Error(`服务器返回错误: ${response.status}`);
    }
    // 成功判定：2xx 且信封 code===200 才算成功，非此一律失败
    if (!body || body.code !== 200) {
      logToOutput(
        `服务器返回业务错误(HTTP ${response.status}): ${rawText}`,
        "error",
      );
      throw new Error(
        `服务器业务错误: ${body && body.msg ? body.msg : "响应体格式异常"}`,
      );
    }
    return body;
  },
  async getNeedSyncOrders(customerId) {
    try {
      logToOutput(`正在从服务器获取需要同步的订单列表...`);
      const version = chrome.runtime.getManifest().version;
      const url = `${CONFIG.API.baseUrl}${CONFIG.API.endpoints.getNeedSyncOrders}?customerId=${encodeURIComponent(customerId)}&v=${encodeURIComponent(version)}`;
      const data = await this.request(url);
      return data.data;
    } catch (error) {
      logToOutput(`获取订单列表失败: ${error.message}`, "error");
      return null;
    }
  },
  async getNeedSyncOrdersTrack(customerId) {
    try {
      logToOutput(`正在从服务器获取需要同步运单号的订单列表...`);
      const url = `${CONFIG.API.baseUrl}${CONFIG.API.endpoints.getNeedSyncOrdersTrack}?customerId=${encodeURIComponent(customerId)}`;
      const data = await this.request(url);
      return data.data;
    } catch (error) {
      logToOutput(`获取运单号订单列表失败: ${error.message}`, "error");
      return null;
    }
  },
  async getPurchaseOrders(customerId) {
    try {
      logToOutput(`正在从服务器获取需要拍单的订单列表...`);
      const version = chrome.runtime.getManifest().version;
      const url = `${CONFIG.API.baseUrl}${CONFIG.API.endpoints.getPurchaseOrders}?customerId=${encodeURIComponent(customerId)}&v=${encodeURIComponent(version)}`;
      // request 已在非 2xx / code!==200 时统一抛错，走到这里必为成功信封
      const data = await this.request(url);
      return data.data || [];
    } catch (error) {
      logToOutput(`获取拍单列表异常: ${error.message}`);
      return [];
    }
  },
  async updatePurchaseOrderFinish(
    id,
    platformOrderNo,
    asins,
    deliveryTime,
    origDeliveryTime,
    creditCardNumber,
    orderNo,
    mainPostCode,
    extPostCode,
    orderPriceInfo,
    extractedProducts,
    execMode,
  ) {
    try {
      logToOutput(
        `正在更新拍单完成信息: 订单=${orderNo}, 采购订单号=${platformOrderNo}, 模式=${execMode}`,
      );
      const result = await Utils.retry(
        async () => {
          const url = `${CONFIG.API.baseUrl}${CONFIG.API.endpoints.purchaseOrderFinishUpdate}`;
          // dry_run 不带金额与单号（ERP对缺失金额不误报）；stop_before_payment/live 才回传详细字段。
          // execMode 在所有调用点都要回显（ERP _resolve_exec_mode 靠回声做档位错配告警）
          const body = { id: id, execMode: execMode };
          if (execMode !== "dry_run") {
            Object.assign(body, {
              platformOrderNo: platformOrderNo,
              asins: asins && asins.length ? asins.join(",") : undefined,
              deliveryTime: deliveryTime,
              origDeliveryTime: origDeliveryTime,
              creditCardNumber: creditCardNumber,
              mainPostCode: mainPostCode,
              extPostCode: extPostCode,
              shipping: orderPriceInfo ? orderPriceInfo.shipping : undefined,
              totalBeforeTax: orderPriceInfo
                ? orderPriceInfo.totalBeforeTax
                : undefined,
              tax: orderPriceInfo ? orderPriceInfo.tax : undefined,
              total: orderPriceInfo ? orderPriceInfo.total : undefined,
              products: extractedProducts,
            });
          }
          return await this.request(url, {
            method: "POST",
            headers: { "Content-Type": "application/json;charset=UTF-8" },
            body: JSON.stringify(body),
          });
        },
        5,
        1e3,
      );
      logToOutput(`拍单完成信息更新成功`);
      return result;
    } catch (error) {
      logToOutput(
        `更新拍单完成信息最终失败，已重试5次: ${error.message}`,
        "error",
      );
      return null;
    }
  },
  async updateOrderStatus(orderId, status, failContent, orderNo) {
    try {
      logToOutput(`正在更新订单状态: 订单=${orderNo}, 原因=${failContent}`);
      const result = await Utils.retry(
        async () => {
          const url = `${CONFIG.API.baseUrl}${CONFIG.API.endpoints.updateOrderStatus}`;
          return await this.request(url, {
            method: "POST",
            body: JSON.stringify({
              orderId: orderId,
              status: status,
              failContent: failContent,
            }),
          });
        },
        3,
        1e3,
      );
      logToOutput(`订单状态更新成功: ${result.msg || "操作完成"}`);
      return result;
    } catch (error) {
      logToOutput(`更新订单状态最终失败，已重试3次: ${error.message}`, "error");
      return null;
    }
  },
  async updateOrderTrackingInfo(orderId, trackingInfo, orderNumber, orderInfo) {
    try {
      logToOutput(`正在更新订单 ${orderNumber} 的跟踪信息...`);
      const result = await Utils.retry(
        async () => {
          const url = `${CONFIG.API.baseUrl}${CONFIG.API.endpoints.updateOrderTracking}`;
          const htmlBase64 = btoa(
            unescape(encodeURIComponent(trackingInfo.html)),
          );
          const trackEvents = trackingInfo.events;
          const formattedDeliveryDate = formatDeliveryTime(
            trackingInfo.deliveryDate,
          );
          const originalDeliveryDate = trackingInfo.deliveryDate;
          const payload = {
            orderId: orderId,
            trackingNumber: trackingInfo.trackingNumber,
            trackingUrl: trackingInfo.url,
            trackingHtml: htmlBase64,
            mainPostCode: orderInfo.mainPostCode,
            extPostCode: orderInfo.extPostCode,
            card: orderInfo.card,
            trackingJson:
              trackEvents.length > 0 ? JSON.stringify(trackEvents) : null,
            asins: orderInfo.asins ? orderInfo.asins.toString() : "",
            orderDate: orderInfo.orderDate,
            subtotal: orderInfo.subtotal,
            shipping: orderInfo.shipping,
            totalBeforeTax: orderInfo.totalBeforeTax,
            tax: orderInfo.tax,
            total: orderInfo.total,
            carrier: trackingInfo.carrier,
            receiptPicture: trackingInfo.deliveryImg,
            deliveryDate: formattedDeliveryDate,
            origDeliveryDate: originalDeliveryDate,
          };
          return await this.request(url, {
            method: "POST",
            body: JSON.stringify(payload),
          });
        },
        3,
        1e3,
      );
      logToOutput(`订单跟踪信息更新成功`);
      return result;
    } catch (error) {
      logToOutput(
        `更新订单跟踪信息最终失败，已重试3次: ${error.message}`,
        "error",
      );
      return null;
    }
  },
  async updateOrderTrackingInfoPig(
    orderId,
    trackingInfo,
    orderNumber,
    orderInfo,
  ) {
    try {
      logToOutput(`正在更新订单 ${orderNumber} 的运单号信息...`);
      const result = await Utils.retry(
        async () => {
          const url = `${CONFIG.API.baseUrl}${CONFIG.API.endpoints.updateOrderTrackingInfo}`;
          const htmlBase64 = btoa(
            unescape(encodeURIComponent(trackingInfo.html)),
          );
          const trackEvents = trackingInfo.events;
          let firstEventTime = null;
          if (trackEvents.length > 0)
            firstEventTime =
              trackEvents[trackEvents.length - 1].day +
              " " +
              trackEvents[trackEvents.length - 1].time;
          const payload = {
            orderId: orderId,
            trackingNumber: trackingInfo.trackingNumber,
            trackingUrl: trackingInfo.url,
            trackingHtml: htmlBase64,
            firstEventTime: firstEventTime,
            mainPostCode: orderInfo.mainPostCode,
            card: orderInfo.card,
            trackingJson:
              trackEvents.length > 0 ? JSON.stringify(trackEvents) : null,
          };
          return await this.request(url, {
            method: "POST",
            body: JSON.stringify(payload),
          });
        },
        3,
        1e3,
      );
      logToOutput(`订单运单号信息更新成功`);
      return result;
    } catch (error) {
      logToOutput(
        `更新订单运单号信息最终失败，已重试3次: ${error.message}`,
        "error",
      );
      return null;
    }
  },
  async updateAmzOrderStatus(orderId, orderNumber, amzStatus) {
    try {
      logToOutput(`正在更新Amazon订单状态: ${orderNumber}`);
      const result = await Utils.retry(
        async () => {
          const url = `${CONFIG.API.baseUrl}${CONFIG.API.endpoints.updateAmzOrderStatus}`;
          return await this.request(url, {
            method: "POST",
            body: JSON.stringify({
              orderId: orderId,
              orderNumber: orderNumber,
              amzStatus: amzStatus,
            }),
          });
        },
        3,
        1e3,
      );
      logToOutput(`Amazon订单状态更新成功`);
      return result;
    } catch (error) {
      logToOutput(
        `更新Amazon订单状态最终失败，已重试3次: ${error.message}`,
        "error",
      );
      return null;
    }
  },
};
const OrderProcessor = {
  async clearCart(iframeDoc) {
    while (true) {
      const trashButtons = iframeDoc.querySelectorAll(
        ".a-declarative > .a-icon.a-icon-small-trash",
      );
      const removeButtons = iframeDoc.querySelectorAll(
        ".a-declarative > .a-icon.a-icon-small-remove",
      );
      let deleteButtons = null;
      if (removeButtons.length > 0 && trashButtons.length > 0) {
        deleteButtons = removeButtons;
      } else if (trashButtons.length > 0) {
        deleteButtons = trashButtons;
      } else if (removeButtons.length > 0) {
        deleteButtons = removeButtons;
      } else {
        deleteButtons = iframeDoc.querySelectorAll(
          ".sc-action-delete-active input",
        );
        if (deleteButtons.length === 0) {
          break;
        }
      }
      deleteButtons[0].click();
      await Utils.sleep(1e3);
    }
  },
  async waitForAddressSave(iframe, order) {
    try {
      const maxWaitTime = CONFIG.TIME.waitTimeout;
      const checkInterval = 500;
      let waitTime = 0;
      while (waitTime < maxWaitTime) {
        await Utils.sleep(checkInterval);
        waitTime += checkInterval;
        if (!iframe || !iframe.contentWindow) {
          logToOutput(`iframe 已失效，无法验证地址保存`, "error");
          return false;
        }
        const iframeDoc = iframe.contentWindow.document;
        const addressElement = iframeDoc.querySelector(
          "#deliver-to-address-text",
        );
        if (addressElement) {
          return true;
        }
      }
      logToOutput(`地址保存验证超时（${maxWaitTime}ms），拍单失败`, "error");
      await ApiService.updateOrderStatus(
        order.id,
        99,
        "地址保存超时",
        order.orderNo,
      );
      await clearShoppingCart();
      return false;
    } catch (error) {
      logToOutput(`等待地址保存时出错: ${error.message}`, "error");
      return false;
    }
  },
};
const UIManager = {
  async updateDrawerContent(drawer) {
    const site = detectAmazonSite();
    const siteInfo = site ? `${site.name}站点` : "未知站点";
    const purchaseSupported = site ? site.purchaseSupported : false;
    const isUSSite = site && site.domain === "amazon.com";
    let siteWarning = "";
    if (!isUSSite) {
      siteWarning =
        '<div class="sb2-site-warning">为保证同步数据完整性，请将站点语言修改为英文</div>';
    }
    drawer.innerHTML = `<div class="sb2-drawer-header"><h2>小蜜蜂</h2><h2>AMZ采购系统</h2><h2>v2.4.1</h2><button class="sb2-drawer-close">×</button></div><div class="sb2-drawer-body"><div class="sb2-site-info">当前站点: ${siteInfo}</div>${siteWarning}<button id="sb2-start-sync" class="sb2-sync-btn">开始同步</button><div class="sb2-config-section"><label class="sb2-config-label">只拍<input type="number" id="sb2-delivery-days-limit" class="sb2-config-input" min="1" max="30" value="7" /><span class="sb2-config-unit">天内送达的产品</span></label></div><button id="sb2-start-purchase" class="sb2-sync-btn ${!purchaseSupported ? "sb2-disabled" : ""}" ${!purchaseSupported ? "disabled" : ""}>开始拍单</button><button id="sb2-extract-customer-id" class="sb2-sync-btn">提取 customerId</button><button id="sb2-start-sync-tracking" class="sb2-sync-btn">Amz To Jc轨迹同步</button><div id="sb2-customer-id-display" class="sb2-customer-id-display"></div><div class="sb2-log-window" id="sb2-log-output"><div class="sb2-log-header">日志输出<button id="sb2-clear-log" class="sb2-clear-log-btn">清空日志</button></div><div class="sb2-log-content"></div></div></div>`;
    this.loadDeliveryDaysLimit();
  },
  loadDeliveryDaysLimit() {
    const savedDays = localStorage.getItem("sb2-delivery-days-limit");
    const input = document.getElementById("sb2-delivery-days-limit");
    if (input && savedDays) {
      input.value = savedDays;
    }
  },
  saveDeliveryDaysLimit(days) {
    localStorage.setItem("sb2-delivery-days-limit", days);
    logToOutput(`预计送达时间限制已更新为 ${days} 天`);
  },
  async createFloatingElements() {
    console.log("UIManager.createFloatingElements() called");
    try {
      const floatBtn = document.createElement("div");
      floatBtn.id = "sb2-float-btn";
      floatBtn.innerHTML = "小蜜蜂";
      floatBtn.className = "sb2-float-btn";
      floatBtn.style.cssText = `position: fixed !important;right: 10px !important;top: 35% !important;transform: translateY(-50%) !important;width: 60px !important;height: 60px !important;background-color: #ecac09 !important;color: white !important;border-radius: 50% !important;text-align: center !important;box-shadow: 2px 2px 8px rgba(0, 0, 0, 0.4) !important;font-weight: bold !important;cursor: pointer !important;border: none !important;z-index: 2147483647 !important;transition: all 0.3s !important;display: flex !important;align-items: center !important;justify-content: center !important;font-size: 12px !important;`;
      document.body.appendChild(floatBtn);
      console.log("Floating button created");
      const drawer = document.createElement("div");
      drawer.id = "sb2-drawer";
      drawer.className = "sb2-drawer";
      drawer.style.cssText = `position: fixed !important; top: 0 !important; right: -350px !important; width: 350px !important; height: 100vh !important; background-color: white !important; box-shadow: -2px 0 10px rgba(0, 0, 0, 0.2) !important; z-index: 2147483647 !important; transition: right 0.3s ease !important; display: flex !important; flex-direction: column !important;`;
      document.body.appendChild(drawer);
      console.log("Drawer created");
      await this.updateDrawerContent(drawer);
      console.log("Drawer content updated");
      this.bindEvents(floatBtn, drawer);
      console.log("Events bound");
      console.log("Floating elements created successfully");
    } catch (error) {
      console.error("Error creating floating elements:", error);
    }
  },
  bindEvents(floatBtn, drawer) {
    floatBtn.addEventListener("click", async () => {
      if (drawer.style.right === "0px") {
        drawer.style.right = "-350px";
      } else {
        drawer.style.right = "0px";
      }
    });
    drawer.addEventListener("click", async (e) => {
      if (e.target.classList.contains("sb2-drawer-close")) {
        drawer.style.right = "-350px";
      } else if (e.target.id === "sb2-start-sync") {
        await this.handleStartSync();
      } else if (e.target.id === "sb2-start-sync-tracking") {
        await this.handleStartSyncTracking();
      } else if (e.target.id === "sb2-start-purchase") {
        await this.handleStartPurchase();
      } else if (e.target.id === "sb2-extract-customer-id") {
        await this.handleExtractCustomerId();
      } else if (e.target.id === "sb2-clear-log") {
        Utils.clearLogOutput();
      }
    });
    const daysLimitInput = document.getElementById("sb2-delivery-days-limit");
    if (daysLimitInput) {
      daysLimitInput.addEventListener("change", (e) => {
        const days = parseInt(e.target.value);
        if (days >= 1 && days <= 30) {
          this.saveDeliveryDaysLimit(days);
        } else {
          logToOutput("天数限制必须在1-30之间", "error");
          e.target.value = localStorage.getItem("sb2-delivery-days-limit") || 7;
        }
      });
    }
  },
  async handleStartSync() {
    const syncButton = document.getElementById("sb2-start-sync");
    if (!syncButton) {
      logToOutput("未找到同步按钮", "error");
      return;
    }
    const originalText = syncButton.textContent;
    const originalClass = syncButton.className;
    try {
      syncButton.textContent = "正在同步中...";
      syncButton.className = "sb2-sync-btn sb2-syncing";
      syncButton.disabled = true;
      const customerId = extractCustomerId();
      if (!customerId) {
        logToOutput("请先提取 customerId", "error");
        return;
      }
      await AppController.handleOrderSync(customerId);
    } catch (error) {
      logToOutput(`同步过程中发生错误: ${error.message}`, "error");
    } finally {
      syncButton.textContent = originalText;
      syncButton.className = originalClass;
      syncButton.disabled = false;
    }
  },
  async handleStartSyncTracking() {
    const syncButton = document.getElementById("sb2-start-sync-tracking");
    if (!syncButton) {
      logToOutput("未找到同步运单号按钮", "error");
      return;
    }
    const originalText = syncButton.textContent;
    const originalClass = syncButton.className;
    try {
      syncButton.textContent = "正在同步中...";
      syncButton.className = "sb2-sync-btn sb2-syncing";
      syncButton.disabled = true;
      const customerId = extractCustomerId();
      if (!customerId) {
        logToOutput("请先提取 customerId", "error");
        return;
      }
      await AppController.handleOrderSyncTracking(customerId);
    } catch (error) {
      logToOutput(`同步过程中发生错误: ${error.message}`, "error");
    } finally {
      syncButton.textContent = originalText;
      syncButton.className = originalClass;
      syncButton.disabled = false;
    }
  },
  async handleStartPurchase() {
    const purchaseButton = document.getElementById("sb2-start-purchase");
    if (!purchaseButton) {
      logToOutput("未找到拍单按钮", "error");
      return;
    }
    const site = detectAmazonSite();
    if (!site) {
      logToOutput("无法识别当前Amazon站点", "error");
      return;
    }
    if (!site.purchaseSupported) {
      logToOutput(`拍单目前仅支持美国站点，其他站点请等待开放`, "error");
      return;
    }
    const originalText = purchaseButton.textContent;
    const originalClass = purchaseButton.className;
    try {
      purchaseButton.textContent = "正在拍单中...";
      purchaseButton.className = "sb2-sync-btn sb2-purchasing";
      purchaseButton.disabled = true;
      const customerId = extractCustomerId();
      if (!customerId) {
        logToOutput("请先提取 customerId", "error");
        return;
      }
      await AppController.handlePurchase(customerId);
    } catch (error) {
      logToOutput(`拍单过程中发生错误: ${error.message}`, "error");
    } finally {
      purchaseButton.textContent = originalText;
      purchaseButton.className = originalClass;
      purchaseButton.disabled = false;
    }
  },
  async handleExtractCustomerId() {
    const customerId = extractCustomerId();
    if (customerId) {
      const display = document.getElementById("sb2-customer-id-display");
      if (display) {
        display.textContent = `CustomerId: ${customerId}`;
        display.style.display = "block";
      }
    }
  },
};
const AppController = {
  init() {
    console.log("AppController.init() called");
    console.log("Current URL:", window.location.href);
    console.log("Is Amazon order page:", isAmazonOrderPage());
    if (isAmazonOrderPage()) {
      console.log("Creating floating elements...");
      UIManager.createFloatingElements();
    } else {
      console.log("Not an Amazon order page, skipping initialization");
    }
  },
  setupEventListeners() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => {
        this.init();
      });
    } else {
      this.init();
    }
  },
  async handleOrderSync(customerId) {
    try {
      logToOutput("开始同步订单...");
      const orders = await ApiService.getNeedSyncOrders(customerId);
      if (!orders || orders.length === 0) {
        logToOutput("没有需要同步的订单");
        return;
      }
      logToOutput(`找到 ${orders.length} 个需要同步的订单`);
      for (let i = 0; i < orders.length; i++) {
        const order = orders[i];
        logToOutput(
          `正在同步第 ${i + 1}/${orders.length} 个订单: ${order.platformOrderNo}`,
        );
        await processOrder(
          order.platformOrderNo,
          order.id,
          order.platformTrackUrl,
          order.platformPostCode,
          order.paymentCard,
          order.logisticsType,
          order.receivingCountry,
        );
        await Utils.sleep(1e3);
      }
      logToOutput("订单同步完成");
    } catch (error) {
      logToOutput(`订单同步失败: ${error.message}`, "error");
    }
  },
  async handleOrderSyncTracking(customerId) {
    try {
      logToOutput("开始同步运单号订单...");
      const orders = await ApiService.getNeedSyncOrdersTrack(customerId);
      if (!orders || orders.length === 0) {
        logToOutput("没有需要同步的运单号订单");
        return;
      }
      logToOutput(`找到 ${orders.length} 个需要同步的运单号订单`);
      for (let i = 0; i < orders.length; i++) {
        const order = orders[i];
        logToOutput(
          `正在同步第 ${i + 1}/${orders.length} 个订单: ${order.platformOrderNo}`,
        );
        await processOrderTracking(
          order.platformOrderNo,
          order.id,
          order.platformTrackUrl,
          order.platformPostCode,
          order.paymentCard,
          order.logisticsType,
          order.receivingCountry,
        );
        await Utils.sleep(1e3);
      }
      logToOutput("运单号订单同步完成");
    } catch (error) {
      logToOutput(`运单号订单同步失败: ${error.message}`, "error");
    }
  },
  async handlePurchase(customerId) {
    try {
      logToOutput("开始拍单...");
      const purchaseOrders = await ApiService.getPurchaseOrders(customerId);
      if (!purchaseOrders || purchaseOrders.length === 0) {
        logToOutput("没有需要拍单的订单");
        return;
      }
      logToOutput(`找到 ${purchaseOrders.length} 个需要拍单的订单`);
      await this.clearShoppingCart();
      logToOutput("购物车清空完成，开始拍单流程...");
      for (let i = 0; i < purchaseOrders.length; i++) {
        const order = purchaseOrders[i];
        logToOutput(
          `处理第 ${i + 1}/${purchaseOrders.length} 条拍单任务: ${order.receivingName}`,
        );
        await this.processPurchaseOrder(order);
      }
      layer.closeAll();
      setTimeout(() => {
        logToOutput("拍单任务全部完成");
      }, 1e3);
    } catch (error) {
      logToOutput(`拍单失败: ${error.message}`, "error");
      layer.closeAll();
    }
  },
  async processPurchaseOrder(order) {
    if (!order.products || order.products.length === 0) {
      logToOutput(`订单 ${order.orderNo} 无产品信息`);
      return;
    }
    markPurchaseStart();
    logStage("处理订单开始");
    let orderSuccess = true;
    let addedProducts = [];
    for (const product of order.products) {
      logStage(`加购 ${product.asin}`);
      const result = await processSingleProduct(
        order,
        product.asin,
        product.quantity,
      );
      if (result.success) {
        addedProducts.push({ asin: product.asin, quantity: product.quantity });
      } else {
        orderSuccess = false;
        logToOutput(`商品 ${product.asin} 处理失败，跳过当前订单`, "error");
        break;
      }
    }
    if (orderSuccess && addedProducts.length > 0) {
      await processCartAndCheckout(order, addedProducts);
    } else {
      logToOutput(`订单 ${order.orderNo} 处理失败，已跳过`, "error");
    }
  },
  async clearShoppingCart() {
    return new Promise((resolve, reject) => {
      try {
        logToOutput("开始清空购物车...");
        const cartUrl = `${window.location.origin}/gp/cart/view.html`;
        const cartLayerIndex = layer.open({
          type: 2,
          title: "清空购物车",
          shade: 0.8,
          closeBtn: 1,
          area: ["1000px", "700px"],
          content: cartUrl,
          success: async function (layero, index) {
            setTimeout(async () => {
              try {
                const iframe = layero.find("iframe")[0];
                if (iframe && iframe.contentWindow) {
                  const iframeDoc = iframe.contentWindow.document;
                  await OrderProcessor.clearCart(iframeDoc);
                  logToOutput("购物车清空完成");
                  resolve();
                } else {
                  reject(new Error("无法访问购物车页面"));
                }
              } catch (error) {
                logToOutput(`清空购物车失败: ${error.message}`, "error");
                reject(error);
              } finally {
                layer.close(cartLayerIndex);
              }
            }, 1e3);
          },
          end: function () {
            resolve();
          },
        });
      } catch (error) {
        logToOutput(`清空购物车失败: ${error.message}`, "error");
        reject(error);
      }
    });
  },
};
async function waitForProductPanels(iframeDoc, products) {
  try {
    const maxWaitTime = 3e4;
    const checkInterval = 500;
    let waitTime = 0;
    while (waitTime < maxWaitTime) {
      await Utils.sleep(checkInterval);
      waitTime += checkInterval;
      let productPanels = iframeDoc.querySelectorAll(
        '[data-csa-c-slot-id="checkout-itemBlockPanel"] .lineitem-container',
      );
      if (productPanels.length > 0) {
        return productPanels;
      }
    }
    logToOutput(`商品信息加载超时（${maxWaitTime}ms）`, "error");
    return null;
  } catch (error) {
    logToOutput(`等待商品信息加载时出错: ${error.message}`, "error");
    return null;
  }
}
async function waitForOrderSuccess(iframe, order) {
  try {
    const maxWaitTime = 3e4;
    const checkInterval = 1e3;
    let waitTime = 0;
    while (waitTime < maxWaitTime) {
      await Utils.sleep(checkInterval);
      waitTime += checkInterval;
      if (!iframe || !iframe.contentWindow) {
        logToOutput(`iframe 已失效，无法验证下单状态`, "error");
        return false;
      }
      try {
        const currentUrl = iframe.contentWindow.location.href;
        if (
          currentUrl.includes("/gp/buy/thankyou/handlers/display.html") ||
          currentUrl.includes("/gp/cart/view.html")
        ) {
          return true;
        }
      } catch (e) {
        logToOutput(`检查页面跳转时出错，继续等待...`);
      }
    }
    logToOutput(`下单验证超时（${maxWaitTime}ms），可能下单失败`, "error");
    await ApiService.updateOrderStatus(
      order.id,
      99,
      "下单验证超时",
      order.orderNo,
    );
    await clearShoppingCart();
    return false;
  } catch (error) {
    logToOutput(`等待下单成功时出错: ${error.message}`, "error");
    return false;
  }
}
function formatDeliveryTime(timeStr) {
  try {
    if (!timeStr || timeStr === "未知") {
      return "未知";
    }
    const now = new Date();
    const parsedDate = parseDeliveryTime(timeStr, now);
    if (parsedDate) {
      const year = parsedDate.getFullYear();
      const month = String(parsedDate.getMonth() + 1).padStart(2, "0");
      const day = String(parsedDate.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    }
    return timeStr;
  } catch (error) {
    logToOutput(`格式化送达时间失败: ${timeStr} - ${error.message}`, "error");
    return timeStr;
  }
}
async function openOrderHistoryPage(
  cartLayerIndex,
  latestDeliveryTime,
  order,
  creditCardNumber,
  orderPriceInfo,
  extractedProducts,
  country,
) {
  return new Promise((resolve) => {
    try {
      const waitForCartClose = () => {
        const checkClosed = setInterval(() => {
          if (!document.querySelector(`[lay-id="${cartLayerIndex}"]`)) {
            clearInterval(checkClosed);
            window.addressFillingInProgress = false;
            setTimeout(() => {
              const orderHistoryUrl = `${window.location.origin}/gp/css/order-history`;
              const orderHistoryLayerIndex = layer.open({
                type: 2,
                title: `订单历史页面`,
                shade: 0.8,
                closeBtn: 1,
                area: ["1000px", "700px"],
                content: orderHistoryUrl,
                success: function (layero, index) {
                  const formattedDeliveryTime =
                    formatDeliveryTime(latestDeliveryTime);
                  logToOutput(`预计送达时间: ${formattedDeliveryTime}`);
                  setTimeout(async () => {
                    try {
                      const iframe = layero.find("iframe")[0];
                      if (iframe && iframe.contentWindow) {
                        const iframeDoc = iframe.contentWindow.document;
                        let orderCards = null;
                        let waitTime = 0;
                        const maxWaitTime = 15e3;
                        const checkInterval = 500;
                        while (waitTime < maxWaitTime) {
                          await Utils.sleep(checkInterval);
                          waitTime += checkInterval;
                          try {
                            orderCards =
                              iframeDoc.querySelectorAll(".js-order-card");
                            if (orderCards.length === 0) {
                              orderCards =
                                iframeDoc.querySelectorAll(".order-card__list");
                            }
                            if (orderCards.length > 0) {
                              break;
                            }
                          } catch (e) {
                            console.log(`等待订单卡片加载时出错，继续等待...`);
                          }
                        }
                        if (!orderCards || orderCards.length === 0) {
                          logToOutput(
                            `订单卡片加载超时，无法找到订单信息`,
                            "error",
                          );
                          await ApiService.updateOrderStatus(
                            order.id,
                            99,
                            "回传订单失败，请手动复制AMZ订单号回填至系统",
                            order.orderNo,
                          );
                          return;
                        }
                        if (orderCards.length > 0) {
                          const firstOrder = orderCards[0];
                          const orderIdSpans = firstOrder.querySelectorAll(
                            ".yohtmlc-order-id span",
                          );
                          let orderId = null;
                          if (orderIdSpans.length >= 2) {
                            orderId = orderIdSpans[1].textContent.trim();
                          } else {
                            logToOutput(`未找到订单号信息`, "error");
                          }
                          const productLinks = firstOrder.querySelectorAll(
                            ".yohtmlc-product-title a",
                          );
                          const asinList = [];
                          for (let i = 0; i < productLinks.length; i++) {
                            const href = productLinks[i].getAttribute("href");
                            if (href) {
                              const asinMatch = href.match(
                                /\b(B0\w{8}|\d{10}|\d{9}X)\b/,
                              );
                              if (asinMatch && asinMatch[1]) {
                                asinList.push(asinMatch[1]);
                              }
                            }
                          }
                          let mainPostCode = null;
                          let extPostCode = null;
                          try {
                            const receivingInfo = firstOrder.querySelector(
                              ".a-popover-trigger.a-declarative.insert-encrypted-trigger-text.aok-break-word i",
                            );
                            receivingInfo.click();
                            await Utils.sleep(1e3);
                            const receivingAddress = iframeDoc
                              .querySelectorAll(
                                ".a-popover-wrapper .a-color-base .a-row",
                              )[1]
                              .innerHTML.trim();
                            let receivingPostCode = null;
                            if (receivingAddress) {
                              const addressParts =
                                receivingAddress.split("<br>");
                              if (country === "JP") {
                                if (addressParts.length > 3) {
                                  receivingPostCode = addressParts[3].trim();
                                } else {
                                  receivingPostCode = addressParts[2].trim();
                                }
                              } else {
                                if (addressParts.length > 2) {
                                  receivingPostCode = addressParts[2].trim();
                                } else {
                                  receivingPostCode = addressParts[1].trim();
                                }
                              }
                            }
                            const matches = receivingPostCode.match(
                              /^(?:([^,]+),\s*)?([^,]+?)\s+(?:(\d{5})(?:-(\d{4}))?|([A-Za-z]\d[A-Za-z]\s\d[A-Za-z]\d)|(\d{3}-\d{4}))$/,
                            );
                            if (matches) {
                              if (matches[3]) {
                                mainPostCode = matches[3];
                                extPostCode = matches[4] || "";
                              } else if (matches[5]) {
                                mainPostCode = matches[5];
                              } else if (matches[6]) {
                                mainPostCode = matches[6];
                              }
                            }
                          } catch (error) {
                            console.log("获取订单邮编信息失败");
                          }
                          if (asinList.length > 0) {
                            if (orderId) {
                              const updateResult =
                                await ApiService.updatePurchaseOrderFinish(
                                  order.id,
                                  orderId,
                                  asinList,
                                  formattedDeliveryTime,
                                  latestDeliveryTime,
                                  creditCardNumber,
                                  order.orderNo,
                                  mainPostCode,
                                  extPostCode,
                                  orderPriceInfo,
                                  extractedProducts,
                                  resolveExecMode(order),
                                );
                              if (updateResult) {
                                logToOutput(`拍单完成信息更新成功`);
                              } else {
                                logToOutput(`拍单完成信息更新失败`, "error");
                              }
                            } else {
                              logToOutput(
                                `订单号获取失败，无法更新服务端信息`,
                                "error",
                              );
                            }
                          } else {
                            logToOutput(`未找到ASIN信息`, "error");
                          }
                        } else {
                          logToOutput(`未找到订单卡片列表`, "error");
                        }
                        layer.close(orderHistoryLayerIndex);
                        resolve(true);
                      } else {
                        logToOutput(`无法获取iframe内容`, "error");
                        layer.close(orderHistoryLayerIndex);
                        resolve(false);
                      }
                    } catch (error) {
                      logToOutput(
                        `获取订单信息时出错: ${error.message}`,
                        "error",
                      );
                      layer.close(orderHistoryLayerIndex);
                      resolve(false);
                    }
                  }, 3e3);
                },
                end: function () {
                  logToOutput(`订单历史页面已关闭`);
                },
              });
            }, 500);
          }
        }, 100);
      };
      layer.close(cartLayerIndex);
      waitForCartClose();
    } catch (error) {
      logToOutput(`打开订单历史页面时出错: ${error.message}`, "error");
      resolve(false);
    }
  });
}
function isParseableDeliveryTime(timeStr) {
  const unparseablePatterns = [
    /arrive\s+after\s+/i,
    /arriving\s+after\s+/i,
    /\bChristmas\b/i,
    /\bEaster\b/i,
    /\bHoliday\b/i,
    /\bNew\s+Year\b/i,
    /\bValentine/i,
    /\bThanksgiving/i,
  ];
  for (const pattern of unparseablePatterns) {
    if (pattern.test(timeStr)) {
      return false;
    }
  }
  return true;
}
function analyzeDeliveryTimes(deliveryTimes) {
  if (deliveryTimes.length === 0) {
    return "未知";
  }
  const parseableTimes = deliveryTimes.filter((timeStr) =>
    isParseableDeliveryTime(timeStr),
  );
  const unparseableTimes = deliveryTimes.filter(
    (timeStr) => !isParseableDeliveryTime(timeStr),
  );
  if (unparseableTimes.length > 0) {
    logToOutput(
      `检测到无法解析的送达时间格式: ${unparseableTimes.join(", ")}`,
      "error",
    );
  }
  if (parseableTimes.length === 0) {
    logToOutput(`所有送达时间都无法解析，使用"未知"`, "error");
    return "未知";
  }
  if (parseableTimes.length === 1) {
    return parseableTimes[0];
  }
  const now = new Date();
  let latestDate = null;
  let latestTimeStr = "";
  for (const timeStr of parseableTimes) {
    const parsedDate = parseDeliveryTime(timeStr, now);
    if (parsedDate && (!latestDate || parsedDate > latestDate)) {
      latestDate = parsedDate;
      latestTimeStr = timeStr;
    }
  }
  return latestTimeStr || parseableTimes[0];
}
function showUnparseableDeliveryTimeConfirm(deliveryTime) {
  return new Promise((resolve) => {
    const message = `当前订单预计送达时间无法正常解析，提取到的预计送达时间为 "${deliveryTime}"，请确认是否继续下单`;
    layer.confirm(message, {
      icon: 3,
      title: "送达时间解析失败",
      btn: ["跳过", "继续下单"],
      btnAlign: "c",
      closeBtn: 0,
      yes: function (index) {
        layer.close(index);
        resolve({ action: "skip" });
      },
      btn2: function (index) {
        layer.close(index);
        resolve({ action: "continue" });
      },
    });
  });
}
const TimezoneUtils = {
  getSiteDate(baseDate = new Date()) {
    const site = detectAmazonSite();
    if (!site) {
      return new Date(
        baseDate.toLocaleString("en-US", { timeZone: "America/Los_Angeles" }),
      );
    }
    const siteDate = new Date(
      baseDate.toLocaleString("en-US", { timeZone: site.timezone }),
    );
    return siteDate;
  },
  getPSTDate(baseDate = new Date()) {
    return this.getSiteDate(baseDate);
  },
  getCurrentSiteTimezone() {
    const site = detectAmazonSite();
    if (!site) {
      return { timezone: "America/Los_Angeles", timezoneName: "美西时区" };
    }
    return { timezone: site.timezone, timezoneName: site.timezoneName };
  },
  convertPSTToUTC(pstDate) {
    const pstOffset = pstDate.getTimezoneOffset();
    const utcDate = new Date(pstDate.getTime() + pstOffset * 6e4);
    return utcDate;
  },
  createSiteDate(year, month, day, hour = 23, minute = 59, second = 59) {
    const site = detectAmazonSite();
    if (!site) {
      return this.createPSTDate(year, month, day, hour, minute, second);
    }
    const siteDateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}`;
    let offset;
    if (site.timezone === "America/Los_Angeles") {
      const isDST = this.isPacificDaylightTime(year, month, day);
      offset = isDST ? "-07:00" : "-08:00";
    } else if (site.timezone === "America/Toronto") {
      const isDST = this.isEasternDaylightTime(year, month, day);
      offset = isDST ? "-04:00" : "-05:00";
    } else if (site.timezone === "Asia/Tokyo") {
      offset = "+09:00";
    } else {
      offset = "+00:00";
    }
    const siteDate = new Date(siteDateStr + offset);
    return siteDate;
  },
  createPSTDate(year, month, day, hour = 23, minute = 59, second = 59) {
    const pstDateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}`;
    const isDST = this.isPacificDaylightTime(year, month, day);
    const offset = isDST ? "-07:00" : "-08:00";
    const pstDate = new Date(pstDateStr + offset);
    return pstDate;
  },
  isEasternDaylightTime(year, month, day) {
    const date = new Date(year, month, day);
    const marchSecondSunday = this.getNthDayOfMonth(year, 2, 0, 2);
    const novemberFirstSunday = this.getNthDayOfMonth(year, 10, 0, 1);
    return date >= marchSecondSunday && date < novemberFirstSunday;
  },
  isPacificDaylightTime(year, month, day) {
    const date = new Date(year, month, day);
    const marchSecondSunday = this.getNthDayOfMonth(year, 2, 0, 2);
    const novemberFirstSunday = this.getNthDayOfMonth(year, 10, 0, 1);
    return date >= marchSecondSunday && date < novemberFirstSunday;
  },
  getNthDayOfMonth(year, month, dayOfWeek, nth) {
    const firstDay = new Date(year, month, 1);
    const firstTargetDay = new Date(firstDay);
    const daysToAdd = (dayOfWeek - firstDay.getDay() + 7) % 7;
    firstTargetDay.setDate(firstDay.getDate() + daysToAdd);
    const nthTargetDay = new Date(firstTargetDay);
    nthTargetDay.setDate(firstTargetDay.getDate() + (nth - 1) * 7);
    return nthTargetDay;
  },
  isDaylightSavingTime(date) {
    const jan = new Date(date.getFullYear(), 0, 1);
    const jul = new Date(date.getFullYear(), 6, 1);
    const stdOffset = Math.max(
      jan.getTimezoneOffset(),
      jul.getTimezoneOffset(),
    );
    return date.getTimezoneOffset() < stdOffset;
  },
};
function parseExplicitDate(timeStr) {
  const dateRangeMatch = timeStr.match(
    /(\w{3})\s+(\d{1,2}),\s+(\d{4})\s*-\s*(\w{3})\s+(\d{1,2}),\s+(\d{4})/,
  );
  if (dateRangeMatch) {
    const [, , , , endMonth, endDay, endYear] = dateRangeMatch;
    const monthMap = {
      Jan: 0,
      Feb: 1,
      Mar: 2,
      Apr: 3,
      May: 4,
      Jun: 5,
      Jul: 6,
      Aug: 7,
      Sep: 8,
      Oct: 9,
      Nov: 10,
      Dec: 11,
    };
    const monthIndex = monthMap[endMonth];
    if (monthIndex !== undefined) {
      const date = new Date(
        parseInt(endYear),
        monthIndex,
        parseInt(endDay),
        23,
        59,
        59,
      );
      return date;
    }
  }
  const singleDateMatch = timeStr.match(/(\w{3})\s+(\d{1,2}),\s+(\d{4})/);
  if (singleDateMatch) {
    const [, month, day, year] = singleDateMatch;
    const monthMap = {
      Jan: 0,
      Feb: 1,
      Mar: 2,
      Apr: 3,
      May: 4,
      Jun: 5,
      Jul: 6,
      Aug: 7,
      Sep: 8,
      Oct: 9,
      Nov: 10,
      Dec: 11,
    };
    const monthIndex = monthMap[month];
    if (monthIndex !== undefined) {
      const date = new Date(
        parseInt(year),
        monthIndex,
        parseInt(day),
        23,
        59,
        59,
      );
      return date;
    }
  }
  const fullMonthDateMatch = timeStr.match(
    /(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?!\s*,\s*\d{4})/,
  );
  if (fullMonthDateMatch) {
    const [, month, day] = fullMonthDateMatch;
    const monthMap = {
      January: 0,
      February: 1,
      March: 2,
      April: 3,
      May: 4,
      June: 5,
      July: 6,
      August: 7,
      September: 8,
      October: 9,
      November: 10,
      December: 11,
    };
    const monthIndex = monthMap[month];
    if (monthIndex !== undefined) {
      const currentYear = new Date().getFullYear();
      let date = new Date(currentYear, monthIndex, parseInt(day), 23, 59, 59);
      const oneMonthLater = new Date();
      oneMonthLater.setMonth(oneMonthLater.getMonth() + 1);
      if (date > oneMonthLater) {
        date = new Date(currentYear - 1, monthIndex, parseInt(day), 23, 59, 59);
      }
      return date;
    }
  }
  const dayFirstMonthMatch = timeStr.match(
    /(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)(?!\s*,\s*\d{4})/,
  );
  if (dayFirstMonthMatch) {
    const [, day, month] = dayFirstMonthMatch;
    const monthMap = {
      January: 0,
      February: 1,
      March: 2,
      April: 3,
      May: 4,
      June: 5,
      July: 6,
      August: 7,
      September: 8,
      October: 9,
      November: 10,
      December: 11,
    };
    const monthIndex = monthMap[month];
    if (monthIndex !== undefined) {
      const currentYear = new Date().getFullYear();
      let date = new Date(currentYear, monthIndex, parseInt(day), 23, 59, 59);
      const oneMonthLater = new Date();
      oneMonthLater.setMonth(oneMonthLater.getMonth() + 1);
      if (date > oneMonthLater) {
        date = new Date(currentYear - 1, monthIndex, parseInt(day), 23, 59, 59);
      }
      return date;
    }
  }
  return null;
}
function parseDeliveryTime(timeStr, baseDate) {
  try {
    const explicitDate = parseExplicitDate(timeStr);
    if (explicitDate) {
      logToOutput(`解析结果: 明确日期 - ${explicitDate.toISOString()}`);
      return explicitDate;
    }
    const siteBaseDate = TimezoneUtils.getSiteDate(baseDate);
    const siteTimezone = TimezoneUtils.getCurrentSiteTimezone();
    logToOutput(
      `解析时间: "${timeStr}", 基准时间: ${baseDate.toISOString()}, ${siteTimezone.timezoneName}: ${siteBaseDate.toISOString()}`,
    );
    if (timeStr.toLowerCase().includes("today")) {
      const today = new Date(siteBaseDate);
      today.setHours(23, 59, 59, 999);
      logToOutput(
        `解析结果: 今天 (${siteTimezone.timezoneName}) - ${today.toISOString()}`,
      );
      return today;
    }
    if (timeStr.toLowerCase().includes("tomorrow")) {
      const tomorrow = new Date(siteBaseDate);
      tomorrow.setDate(tomorrow.getDate() + 1);
      tomorrow.setHours(23, 59, 59, 999);
      logToOutput(
        `解析结果: 明天 (${siteTimezone.timezoneName}) - ${tomorrow.toISOString()}`,
      );
      return tomorrow;
    }
    const weekdayMatch = timeStr.match(
      /\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b/i,
    );
    if (weekdayMatch) {
      const targetWeekday = weekdayMatch[1].toLowerCase();
      const weekdayMap = {
        monday: 1,
        tuesday: 2,
        wednesday: 3,
        thursday: 4,
        friday: 5,
        saturday: 6,
        sunday: 0,
      };
      const targetDay = weekdayMap[targetWeekday];
      const currentDay = siteBaseDate.getDay();
      let daysToAdd = (targetDay - currentDay + 7) % 7;
      if (daysToAdd === 0) {
        const currentHour = siteBaseDate.getHours();
        if (currentHour >= 18) {
          daysToAdd = 7;
        }
      }
      const targetDate = new Date(siteBaseDate);
      targetDate.setDate(siteBaseDate.getDate() + daysToAdd);
      targetDate.setHours(23, 59, 59, 999);
      logToOutput(
        `解析结果: ${weekdayMatch[1]} (${siteTimezone.timezoneName}) - ${targetDate.toISOString()}`,
      );
      return targetDate;
    }
    const dateRangeMatch = timeStr.match(
      /(\w{3})\s+(\d{1,2}),\s+(\d{4})\s*-\s*(\w{3})\s+(\d{1,2}),\s+(\d{4})/,
    );
    if (dateRangeMatch) {
      const [, , , , endMonth, endDay, endYear] = dateRangeMatch;
      const monthMap = {
        Jan: 0,
        Feb: 1,
        Mar: 2,
        Apr: 3,
        May: 4,
        Jun: 5,
        Jul: 6,
        Aug: 7,
        Sep: 8,
        Oct: 9,
        Nov: 10,
        Dec: 11,
      };
      const monthIndex = monthMap[endMonth];
      if (monthIndex !== undefined) {
        const date = TimezoneUtils.createSiteDate(
          parseInt(endYear),
          monthIndex,
          parseInt(endDay),
        );
        return date;
      }
    }
    const multipleDateMatch = timeStr.match(
      /(\w{3})\s+(\d{1,2})(?:\s+and\s+\w+,\s+(\w{3})\s+(\d{1,2}))?/,
    );
    if (multipleDateMatch) {
      let month, day, year;
      if (multipleDateMatch[3] && multipleDateMatch[4]) {
        month = multipleDateMatch[3];
        day = multipleDateMatch[4];
      } else {
        month = multipleDateMatch[1];
        day = multipleDateMatch[2];
      }
      year = siteBaseDate.getFullYear();
      const monthMap = {
        Jan: 0,
        Feb: 1,
        Mar: 2,
        Apr: 3,
        May: 4,
        Jun: 5,
        Jul: 6,
        Aug: 7,
        Sep: 8,
        Oct: 9,
        Nov: 10,
        Dec: 11,
      };
      const monthIndex = monthMap[month];
      if (monthIndex !== undefined) {
        let date = TimezoneUtils.createSiteDate(
          year,
          monthIndex,
          parseInt(day),
        );
        const oneMonthLater = new Date(siteBaseDate);
        oneMonthLater.setMonth(oneMonthLater.getMonth() + 1);
        if (date > oneMonthLater) {
          date = TimezoneUtils.createSiteDate(
            year - 1,
            monthIndex,
            parseInt(day),
          );
        }
        return date;
      }
    }
    const singleDateMatch = timeStr.match(/(\w{3})\s+(\d{1,2}),\s+(\d{4})/);
    if (singleDateMatch) {
      const [, month, day, year] = singleDateMatch;
      const monthMap = {
        Jan: 0,
        Feb: 1,
        Mar: 2,
        Apr: 3,
        May: 4,
        Jun: 5,
        Jul: 6,
        Aug: 7,
        Sep: 8,
        Oct: 9,
        Nov: 10,
        Dec: 11,
      };
      const monthIndex = monthMap[month];
      if (monthIndex !== undefined) {
        const date = TimezoneUtils.createSiteDate(
          parseInt(year),
          monthIndex,
          parseInt(day),
        );
        return date;
      }
    }
    const noYearDateMatch = timeStr.match(
      /(\w{3})\s+(\d{1,2})(?!\s*,\s*\d{4})/,
    );
    if (noYearDateMatch) {
      const [, month, day] = noYearDateMatch;
      const monthMap = {
        Jan: 0,
        Feb: 1,
        Mar: 2,
        Apr: 3,
        May: 4,
        Jun: 5,
        Jul: 6,
        Aug: 7,
        Sep: 8,
        Oct: 9,
        Nov: 10,
        Dec: 11,
      };
      const monthIndex = monthMap[month];
      if (monthIndex !== undefined) {
        const year = siteBaseDate.getFullYear();
        let date = TimezoneUtils.createSiteDate(
          year,
          monthIndex,
          parseInt(day),
        );
        const oneMonthLater = new Date(siteBaseDate);
        oneMonthLater.setMonth(oneMonthLater.getMonth() + 1);
        if (date > oneMonthLater) {
          date = TimezoneUtils.createSiteDate(
            year - 1,
            monthIndex,
            parseInt(day),
          );
        }
        return date;
      }
    }
    const fullMonthDateMatch = timeStr.match(
      /(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?!\s*,\s*\d{4})/,
    );
    if (fullMonthDateMatch) {
      const [, month, day] = fullMonthDateMatch;
      const monthMap = {
        January: 0,
        February: 1,
        March: 2,
        April: 3,
        May: 4,
        June: 5,
        July: 6,
        August: 7,
        September: 8,
        October: 9,
        November: 10,
        December: 11,
      };
      const monthIndex = monthMap[month];
      if (monthIndex !== undefined) {
        const year = siteBaseDate.getFullYear();
        let date = TimezoneUtils.createSiteDate(
          year,
          monthIndex,
          parseInt(day),
        );
        const oneMonthLater = new Date(siteBaseDate);
        oneMonthLater.setMonth(oneMonthLater.getMonth() + 1);
        if (date > oneMonthLater) {
          date = TimezoneUtils.createSiteDate(
            year - 1,
            monthIndex,
            parseInt(day),
          );
        }
        return date;
      }
    }
    const dayFirstMonthMatch = timeStr.match(
      /(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)(?!\s*,\s*\d{4})/,
    );
    if (dayFirstMonthMatch) {
      const [, day, month] = dayFirstMonthMatch;
      const monthMap = {
        January: 0,
        February: 1,
        March: 2,
        April: 3,
        May: 4,
        June: 5,
        July: 6,
        August: 7,
        September: 8,
        October: 9,
        November: 10,
        December: 11,
      };
      const monthIndex = monthMap[month];
      if (monthIndex !== undefined) {
        const year = siteBaseDate.getFullYear();
        let date = TimezoneUtils.createSiteDate(
          year,
          monthIndex,
          parseInt(day),
        );
        const oneMonthLater = new Date(siteBaseDate);
        oneMonthLater.setMonth(oneMonthLater.getMonth() + 1);
        if (date > oneMonthLater) {
          date = TimezoneUtils.createSiteDate(
            year - 1,
            monthIndex,
            parseInt(day),
          );
        }
        return date;
      }
    }
    const parsed = new Date(timeStr);
    if (!isNaN(parsed.getTime())) {
      return parsed;
    }
    return null;
  } catch (error) {
    logToOutput(`解析送达时间失败: ${timeStr} - ${error.message}`, "error");
    return null;
  }
}
async function verifyPageInfo(iframeDoc, order, iframe, cartLayerIndex) {
  try {
    const addressElement = iframeDoc.querySelector("#deliver-to-address-text");
    if (!addressElement) {
      logToOutput(`未找到地址信息元素`, "error");
      return false;
    }
    const addressText = addressElement.textContent;
    if (!addressText.includes(order.receivingPostCode)) {
      logToOutput(
        `地址验证失败: 地址中不包含邮编 ${order.receivingPostCode}`,
        "error",
      );
      return false;
    }
    logToOutput(`地址验证通过`);
    const paymentElement = iframeDoc.querySelector(
      "#payment-option-text-default",
    );
    if (!paymentElement) {
      logToOutput(`未找到付款信息元素`, "error");
      return false;
    }
    const paymentText = paymentElement.textContent;
    const cardNumberMatch = paymentText.match(/\d{4}/);
    if (!cardNumberMatch) {
      logToOutput(`未找到卡号信息`, "error");
      return false;
    }
    const creditCardNumber = cardNumberMatch[0];
    const productPanels = await waitForProductPanels(iframeDoc, order.products);
    if (!productPanels || productPanels.length === 0) {
      logToOutput(`未找到商品信息`, "error");
      return false;
    }
    const extractedProducts = [];
    let totalCalculatedPrice = 0;
    const deliveryTimes = [];
    for (let i = 0; i < productPanels.length; i++) {
      const panel = productPanels[i];
      const asinElement = iframeDoc.querySelectorAll(
        "#col-item-block-description > .aok-hidden",
      );
      if (!asinElement) {
        logToOutput(`商品 ${i + 1} 未找到ASIN信息`, "error");
        return false;
      }
      const asin = asinElement[i].textContent.trim();
      const priceElement = panel.querySelector(
        "span.lineitem-price-text.a-text-bold",
      );
      if (!priceElement) {
        logToOutput(`商品 ${asin} 未找到价格信息`, "error");
        return false;
      }
      const priceText = priceElement.textContent.trim();
      const priceMatch = priceText.match(/[\d,]+\.?\d*/);
      const unitPrice = priceMatch
        ? parseFloat(priceMatch[0].replace(/,/g, ""))
        : 0;
      const orderProduct = order.products.find((p) => p.asin === asin);
      if (!orderProduct) {
        logToOutput(
          `订单中未找到商品 ${asin}，如购买商品属于捆绑商品（bundle），请手动拍单。`,
          "error",
        );
        return false;
      }
      const quantity = orderProduct.quantity;
      const productTotal = unitPrice * quantity;
      totalCalculatedPrice += productTotal;
      const productImageElement = panel.querySelector("img");
      if (!productImageElement) {
        logToOutput(`商品 ${asin} 未找到图片信息`, "error");
        return false;
      }
      const productImage = productImageElement.src;
      extractedProducts.push({
        asin: asin,
        quantity: quantity,
        unitPrice: unitPrice,
        totalPrice: productTotal,
        productImage: productImage,
      });
      logToOutput(
        `商品 ${asin}: 单价$${unitPrice}, 数量${quantity}, 小计$${productTotal}`,
      );
      const deliveryMethodElement = panel.querySelector(
        "p.a-spacing-none span.a-size-small",
      );
      if (!deliveryMethodElement) {
        logToOutput(`商品 ${asin} 未找到配送方式信息`, "error");
        return false;
      }
      const deliveryMethodText = deliveryMethodElement.textContent
        .trim()
        .toLowerCase();
      if (!deliveryMethodText.includes("amazon")) {
        logToOutput(`商品 ${asin} 配送方式非FBA`, "error");
        await ApiService.updateOrderStatus(
          order.id,
          99,
          `商品${asin}配送方式非FBA`,
          order.orderNo,
        );
        return false;
      }
    }
    if (extractedProducts.length !== order.products.length) {
      logToOutput(
        `商品数量不匹配: 页面${extractedProducts.length}, 订单${order.products.length}`,
        "error",
      );
      return false;
    }
    for (const orderProduct of order.products) {
      const extractedProduct = extractedProducts.find(
        (p) => p.asin === orderProduct.asin,
      );
      if (!extractedProduct) {
        logToOutput(`未找到商品 ${orderProduct.asin}`, "error");
        return false;
      }
      if (extractedProduct.quantity !== orderProduct.quantity) {
        logToOutput(
          `商品 ${orderProduct.asin} 数量不匹配: 页面${extractedProduct.quantity}, 订单${orderProduct.quantity}`,
          "error",
        );
        return false;
      }
    }
    let totalPriceElement = iframeDoc.querySelector(
      "#checkout-pyo-button-block .a-column.a-span12.grand-total-cell",
    );
    if (!totalPriceElement) {
      totalPriceElement = iframeDoc.querySelector(
        "#checkout-pyo-button-block #subtotals-transactional-tango-bottom .grand-total-cell",
      );
      if (!totalPriceElement) {
        logToOutput(`未找到订单总价信息`, "error");
        return false;
      }
    }
    const totalPriceText = totalPriceElement.textContent.trim();
    const totalPriceMatch = totalPriceText.match(/[\d,]+\.?\d*/);
    const orderTotalPrice = totalPriceMatch
      ? parseFloat(totalPriceMatch[0].replace(/,/g, ""))
      : 0;
    const priceDifference = Math.abs(orderTotalPrice - totalCalculatedPrice);
    const priceDifferencePercent = (priceDifference / orderTotalPrice) * 100;
    if (priceDifferencePercent > 50) {
      logToOutput(
        `金额验证失败: 差异超过50% (${priceDifferencePercent.toFixed(2)}%)`,
        "error",
      );
      return false;
    }
    const orderPriceInfo = {
      shipping: "0.00",
      totalBeforeTax: "0.00",
      tax: "0.00",
      total: "0.00",
    };
    const priceElements = iframeDoc.querySelectorAll(
      "#subtotals-marketplace-table li",
    );
    for (let i = 0; i < priceElements.length; i++) {
      const itemElement = priceElements[i].querySelector(
        ".order-summary-line-term",
      );
      const valueElement = priceElements[i].querySelector(
        ".order-summary-line-definition",
      );
      if (!itemElement || !valueElement) {
        continue;
      }
      const item = itemElement.textContent.trim().toLowerCase();
      const value = valueElement.textContent.trim();
      if (item === "shipping & handling:") {
        orderPriceInfo.shipping = value;
      } else if (item === "total before tax:" || item === "items:") {
        orderPriceInfo.totalBeforeTax = value;
      } else if (
        item === "estimated tax to be collected:" ||
        item === "estimated gst/hst:"
      ) {
        orderPriceInfo.tax = value;
      } else if (item === "order total:") {
        orderPriceInfo.total = value;
      }
    }
    let deliveryElements = iframeDoc.querySelectorAll(
      '[data-csa-c-slot-id="checkout-itemBlockPanel"] h2',
    );
    let deliveryTime = "未知";
    if (deliveryElements) {
      for (let i = 0; i < deliveryElements.length; i++) {
        deliveryTime = deliveryElements[i].textContent.trim();
        if (deliveryTime !== "未知") {
          deliveryTimes.push(deliveryTime);
        }
      }
    }
    const latestDeliveryTime = analyzeDeliveryTimes(deliveryTimes);
    const daysLimit = Utils.getDeliveryDaysLimit();
    let needConfirm = false;
    let displayDeliveryTime = latestDeliveryTime;
    if (latestDeliveryTime !== "未知") {
      const now = new Date();
      const parsedDeliveryDate = parseDeliveryTime(latestDeliveryTime, now);
      if (parsedDeliveryDate) {
        const siteNow = TimezoneUtils.getSiteDate(now);
        const siteTimezone = TimezoneUtils.getCurrentSiteTimezone();
        const timeDiff = parsedDeliveryDate.getTime() - siteNow.getTime();
        const daysDiff = Math.floor(timeDiff / (1e3 * 60 * 60 * 24));
        logToOutput(
          `预计送达时间: ${latestDeliveryTime}，距离现在 ${daysDiff} 天`,
        );
        if (daysDiff > daysLimit) {
          logToOutput(
            `预计送达时间超过${daysLimit}天（${siteTimezone.timezoneName}），取消下单`,
            "error",
          );
          await ApiService.updateOrderStatus(
            order.id,
            99,
            `预计送达时间超过${daysLimit}天`,
            order.orderNo,
          );
          await clearShoppingCart();
          return false;
        }
      } else {
        needConfirm = true;
        displayDeliveryTime = latestDeliveryTime;
        logToOutput(`警告: 无法解析送达时间 "${latestDeliveryTime}"`, "error");
      }
    } else {
      if (deliveryTimes.length > 0) {
        needConfirm = true;
        displayDeliveryTime = deliveryTimes[0];
        logToOutput(
          `未获取到可解析的送达时间，原始时间: ${displayDeliveryTime}`,
          "error",
        );
      }
    }
    if (needConfirm) {
      const confirmResult =
        await showUnparseableDeliveryTimeConfirm(displayDeliveryTime);
      if (confirmResult.action === "skip") {
        logToOutput(`用户选择跳过订单，原因：预计送达时间解析失败`);
        await ApiService.updateOrderStatus(
          order.id,
          99,
          "预计送达时间解析失败，用户主动跳过订单",
          order.orderNo,
        );
        await clearShoppingCart();
        return false;
      } else if (confirmResult.action === "continue") {
        logToOutput(`用户选择继续下单，忽略送达时间解析失败`);
      }
    }
    logToOutput(`开始下单操作...`);
    await Utils.sleep(1e3);
    const placeOrderButton = iframeDoc.querySelector(
      "#submitOrderButtonId input",
    );
    if (placeOrderButton) {
      const execMode = resolveExecMode(order);
      if (execMode === "stop_before_payment") {
        // 走完结账页、抓完实付金额四件+预计送达+卡后四位，绝不点击下单按钮
        logToOutput(
          `stop_before_payment 模式：已停在付款前一步，不点击下单按钮`,
        );
        const finishResult = await ApiService.updatePurchaseOrderFinish(
          order.id,
          undefined, // platformOrderNo：未下单没有单号（带了ERP会按live入账并报警）
          undefined, // asins：未进入订单历史页，无法获取
          formatDeliveryTime(latestDeliveryTime),
          latestDeliveryTime,
          creditCardNumber,
          order.orderNo,
          undefined, // mainPostCode：未进入订单历史页，无法获取
          undefined, // extPostCode
          orderPriceInfo,
          extractedProducts,
          execMode,
        );
        if (finishResult) {
          logToOutput(`stop_before_payment 完成信息回传成功`);
        } else {
          logToOutput(`stop_before_payment 完成信息回传失败`, "error");
        }
        return true;
      }
      placeOrderButton.click();
      const orderSuccess = await waitForOrderSuccess(iframe, order);
      if (!orderSuccess) {
        logToOutput(`下单失败`, "error");
        return false;
      }
      const orderHistoryResult = await openOrderHistoryPage(
        cartLayerIndex,
        latestDeliveryTime,
        order,
        creditCardNumber,
        orderPriceInfo,
        extractedProducts,
        order.receivingCountry,
      );
      if (!orderHistoryResult) {
        logToOutput(`打开订单历史页面失败`, "error");
        return false;
      }
    } else {
      logToOutput(`未找到下单按钮`, "error");
      return false;
    }
    return true;
  } catch (error) {
    logToOutput(`验证页面信息失败: ${error.message}`, "error");
    return false;
  }
}
async function verifyPageInfoJp(iframeDoc, order, iframe, cartLayerIndex) {
  try {
    logToOutput(`等待下单按钮出现...`);
    const placeOrderButton = await waitForElement(
      iframeDoc,
      "#submitOrderButtonId input",
    );
    if (!placeOrderButton) {
      logToOutput(`未找到下单按钮`, "error");
      return false;
    }
    logToOutput(`下单按钮已出现，等待用户确认并点击...`);
    await waitForButtonClick(placeOrderButton, iframeDoc);
    logToOutput(`检测到用户点击下单按钮，开始验证页面信息...`);
    const addressElement = iframeDoc.querySelector("#deliver-to-address-text");
    if (!addressElement) {
      logToOutput(`未找到地址信息元素`, "error");
      hideVerificationOverlay(iframeDoc);
      return false;
    }
    const addressText = addressElement.textContent;
    if (!addressText.includes(order.receivingPostCode)) {
      logToOutput(
        `地址验证失败: 地址中不包含邮编 ${order.receivingPostCode}`,
        "error",
      );
      hideVerificationOverlay(iframeDoc);
      return false;
    }
    logToOutput(`地址验证通过`);
    const paymentElement = iframeDoc.querySelector(
      "#payment-option-text-default",
    );
    if (!paymentElement) {
      logToOutput(`未找到付款信息元素`, "error");
      hideVerificationOverlay(iframeDoc);
      return false;
    }
    const paymentText = paymentElement.textContent;
    let creditCardNumber = "";
    if (paymentText.includes("Paying with Amazon Points")) {
      creditCardNumber = "礼品卡";
      logToOutput(`检测到使用礼品卡/积分支付`);
    } else {
      const cardNumberMatch = paymentText.match(/\d{4}/);
      if (!cardNumberMatch) {
        logToOutput(`未找到卡号信息`, "error");
        hideVerificationOverlay(iframeDoc);
        return false;
      }
      creditCardNumber = cardNumberMatch[0];
    }
    const productPanels = await waitForProductPanels(iframeDoc, order.products);
    if (!productPanels || productPanels.length === 0) {
      logToOutput(`未找到商品信息`, "error");
      hideVerificationOverlay(iframeDoc);
      return false;
    }
    const extractedProducts = [];
    let totalCalculatedPrice = 0;
    const deliveryTimes = [];
    for (let i = 0; i < productPanels.length; i++) {
      const panel = productPanels[i];
      const asinElement = iframeDoc.querySelectorAll(
        "#col-item-block-description > .aok-hidden",
      );
      if (!asinElement) {
        logToOutput(`商品 ${i + 1} 未找到ASIN信息`, "error");
        hideVerificationOverlay(iframeDoc);
        return false;
      }
      const asin = asinElement[i].textContent.trim();
      const priceElement = panel.querySelector(
        "span.lineitem-price-text.a-text-bold",
      );
      if (!priceElement) {
        logToOutput(`商品 ${asin} 未找到价格信息`, "error");
        hideVerificationOverlay(iframeDoc);
        return false;
      }
      const priceText = priceElement.textContent.trim();
      const priceMatch = priceText.match(/[\d,]+\.?\d*/);
      const unitPrice = priceMatch
        ? parseFloat(priceMatch[0].replace(/,/g, ""))
        : 0;
      const orderProduct = order.products.find((p) => p.asin === asin);
      if (!orderProduct) {
        logToOutput(
          `订单中未找到商品 ${asin}，如购买商品属于捆绑商品（bundle），请手动拍单。`,
          "error",
        );
        hideVerificationOverlay(iframeDoc);
        return false;
      }
      const quantity = orderProduct.quantity;
      const productTotal = unitPrice * quantity;
      totalCalculatedPrice += productTotal;
      const productImageElement = panel.querySelector("img");
      if (!productImageElement) {
        logToOutput(`商品 ${asin} 未找到图片信息`, "error");
        hideVerificationOverlay(iframeDoc);
        return false;
      }
      const productImage = productImageElement.src;
      extractedProducts.push({
        asin: asin,
        quantity: quantity,
        unitPrice: unitPrice,
        totalPrice: productTotal,
        productImage: productImage,
      });
      logToOutput(
        `商品 ${asin}: 单价¥${unitPrice}, 数量${quantity}, 小计¥${productTotal}`,
      );
      const deliveryMethodElement = panel.querySelector(
        "p.a-spacing-none span.a-size-small",
      );
      if (!deliveryMethodElement) {
        logToOutput(`商品 ${asin} 未找到配送方式信息`, "error");
        hideVerificationOverlay(iframeDoc);
        return false;
      }
      const deliveryMethodText = deliveryMethodElement.textContent
        .trim()
        .toLowerCase();
      if (!deliveryMethodText.includes("amazon")) {
        logToOutput(`商品 ${asin} 配送方式非FBA`, "error");
        hideVerificationOverlay(iframeDoc);
        await ApiService.updateOrderStatus(
          order.id,
          99,
          `商品${asin}配送方式非FBA`,
          order.orderNo,
        );
        return false;
      }
    }
    if (extractedProducts.length !== order.products.length) {
      logToOutput(
        `商品数量不匹配: 页面${extractedProducts.length}, 订单${order.products.length}`,
        "error",
      );
      hideVerificationOverlay(iframeDoc);
      return false;
    }
    for (const orderProduct of order.products) {
      const extractedProduct = extractedProducts.find(
        (p) => p.asin === orderProduct.asin,
      );
      if (!extractedProduct) {
        logToOutput(`未找到商品 ${orderProduct.asin}`, "error");
        hideVerificationOverlay(iframeDoc);
        return false;
      }
      if (extractedProduct.quantity !== orderProduct.quantity) {
        logToOutput(
          `商品 ${orderProduct.asin} 数量不匹配: 页面${extractedProduct.quantity}, 订单${orderProduct.quantity}`,
          "error",
        );
        hideVerificationOverlay(iframeDoc);
        return false;
      }
    }
    const orderPriceInfo = {
      shipping: "0.00",
      totalBeforeTax: "0.00",
      tax: "0.00",
      total: "0.00",
      paymentTotal: "0.00",
    };
    let priceElements = iframeDoc.querySelectorAll(
      "#subtotals-transactional-table li",
    );
    if (priceElements.length === 0) {
      priceElements = iframeDoc.querySelectorAll(
        "#subtotals-marketplace-table li",
      );
    }
    for (let i = 0; i < priceElements.length; i++) {
      const itemElement = priceElements[i].querySelector(
        ".order-summary-line-term",
      );
      const valueElement = priceElements[i].querySelector(
        ".order-summary-line-definition",
      );
      if (!itemElement || !valueElement) {
        continue;
      }
      const item = itemElement.textContent.trim().toLowerCase();
      const value = valueElement.textContent.trim();
      if (item === "shipping & handling:") {
        orderPriceInfo.shipping = value;
      } else if (
        item === "total before tax:" ||
        item === "items:" ||
        item === "item(s) subtotal:"
      ) {
        orderPriceInfo.totalBeforeTax = value;
      } else if (
        item === "estimated tax to be collected:" ||
        item === "estimated gst/hst:"
      ) {
        orderPriceInfo.tax = value;
      } else if (item === "order total:") {
        orderPriceInfo.total = value;
      } else if (item === "payment grand total:" || item === "payment total:") {
        orderPriceInfo.paymentTotal = value;
      }
    }
    let deliveryElements = iframeDoc.querySelectorAll(
      '[data-csa-c-slot-id="checkout-itemBlockPanel"] h2',
    );
    let deliveryTime = "未知";
    if (deliveryElements) {
      for (let i = 0; i < deliveryElements.length; i++) {
        deliveryTime = deliveryElements[i].textContent.trim();
        if (deliveryTime !== "未知") {
          deliveryTimes.push(deliveryTime);
        }
      }
    }
    const latestDeliveryTime = analyzeDeliveryTimes(deliveryTimes);
    const daysLimit = Utils.getDeliveryDaysLimit();
    let needConfirm = false;
    let displayDeliveryTime = latestDeliveryTime;
    if (latestDeliveryTime !== "未知") {
      const now = new Date();
      const parsedDeliveryDate = parseDeliveryTime(latestDeliveryTime, now);
      if (parsedDeliveryDate) {
        const siteNow = TimezoneUtils.getSiteDate(now);
        const siteTimezone = TimezoneUtils.getCurrentSiteTimezone();
        const timeDiff = parsedDeliveryDate.getTime() - siteNow.getTime();
        const daysDiff = Math.floor(timeDiff / (1e3 * 60 * 60 * 24));
        logToOutput(
          `预计送达时间: ${latestDeliveryTime}，距离现在 ${daysDiff} 天`,
        );
        if (daysDiff > daysLimit) {
          logToOutput(
            `预计送达时间超过${daysLimit}天（${siteTimezone.timezoneName}），取消下单`,
            "error",
          );
          hideVerificationOverlay(iframeDoc);
          await ApiService.updateOrderStatus(
            order.id,
            99,
            `预计送达时间超过${daysLimit}天`,
            order.orderNo,
          );
          await clearShoppingCart();
          return false;
        }
      } else {
        needConfirm = true;
        displayDeliveryTime = latestDeliveryTime;
        logToOutput(`警告: 无法解析送达时间 "${latestDeliveryTime}"`, "error");
      }
    } else {
      if (deliveryTimes.length > 0) {
        needConfirm = true;
        displayDeliveryTime = deliveryTimes[0];
        logToOutput(
          `未获取到可解析的送达时间，原始时间: ${displayDeliveryTime}`,
          "error",
        );
      }
    }
    if (needConfirm) {
      const confirmResult =
        await showUnparseableDeliveryTimeConfirm(displayDeliveryTime);
      if (confirmResult.action === "skip") {
        logToOutput(`用户选择跳过订单，原因：预计送达时间解析失败`);
        hideVerificationOverlay(iframeDoc);
        await ApiService.updateOrderStatus(
          order.id,
          99,
          "预计送达时间解析失败，用户主动跳过订单",
          order.orderNo,
        );
        await clearShoppingCart();
        return false;
      } else if (confirmResult.action === "continue") {
        logToOutput(`用户选择继续下单，忽略送达时间解析失败`);
      }
    }
    logToOutput(`所有验证通过，允许下单`);
    hideVerificationOverlay(iframeDoc);
    // JP 链同款三档闸：人机接力验证走完、金额/送达/卡后四位已抓齐，本档绝不点击
    // 下单按钮——JP 的下单点击在本函数内（非 JP 在 verifyPageInfo），两处都要设闸
    const execModeJp = resolveExecMode(order);
    if (execModeJp === "stop_before_payment") {
      logToOutput(`stop_before_payment 模式：已停在付款前一步，不点击下单按钮`);
      const finishResult = await ApiService.updatePurchaseOrderFinish(
        order.id,
        undefined, // platformOrderNo：未下单没有单号（带了ERP会按live入账并报警）
        undefined, // asins：未进入订单历史页，无法获取
        formatDeliveryTime(latestDeliveryTime),
        latestDeliveryTime,
        creditCardNumber,
        order.orderNo,
        undefined, // mainPostCode：未进入订单历史页，无法获取
        undefined, // extPostCode
        orderPriceInfo,
        extractedProducts,
        execModeJp,
      );
      if (finishResult) {
        logToOutput(`stop_before_payment 完成信息回传成功`);
      } else {
        logToOutput(`stop_before_payment 完成信息回传失败`, "error");
      }
      return true;
    }
    logToOutput(`开始下单操作...`);
    await Utils.sleep(500);
    placeOrderButton.click();
    const orderSuccess = await waitForOrderSuccess(iframe, order);
    if (!orderSuccess) {
      logToOutput(`下单失败`, "error");
      return false;
    }
    const orderHistoryResult = await openOrderHistoryPage(
      cartLayerIndex,
      latestDeliveryTime,
      order,
      creditCardNumber,
      orderPriceInfo,
      extractedProducts,
      order.receivingCountry,
    );
    if (!orderHistoryResult) {
      logToOutput(`打开订单历史页面失败`, "error");
      return false;
    }
    return true;
  } catch (error) {
    logToOutput(`验证页面信息失败: ${error.message}`, "error");
    hideVerificationOverlay(iframeDoc);
    return false;
  }
}
async function waitForElement(doc, selector, checkInterval = 500) {
  return new Promise((resolve) => {
    const element = doc.querySelector(selector);
    if (element) {
      resolve(element);
      return;
    }
    const intervalId = setInterval(() => {
      const element = doc.querySelector(selector);
      if (element) {
        clearInterval(intervalId);
        resolve(element);
      }
    }, checkInterval);
  });
}
async function waitForButtonClick(button, iframeDoc) {
  return new Promise((resolve) => {
    const clickHandler = (event) => {
      event.preventDefault();
      // stopImmediatePropagation 而非 stopPropagation：这颗下单按钮是全链唯一真花钱的
      // 动作，若亚马逊在同元素上挂了自己的 click 监听，仅 stopPropagation 挡不住同元素
      // 上排在本监听之前的其它监听器；stopImmediatePropagation 连它们一并拦下。
      event.stopImmediatePropagation();
      button.removeEventListener("click", clickHandler, true);
      showVerificationOverlay(iframeDoc);
      resolve(true);
    };
    button.addEventListener("click", clickHandler, true);
  });
}
function showVerificationOverlay(iframeDoc) {
  const overlay = iframeDoc.createElement("div");
  overlay.id = "sb2-verification-overlay";
  overlay.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background-color: rgba(0, 0, 0, 0.7);
    z-index: 999999;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    color: white;
    font-size: 18px;
    font-weight: bold;
  `;
  const spinner = iframeDoc.createElement("div");
  spinner.style.cssText = `
    border: 5px solid #f3f3f3;
    border-top: 5px solid #ecac09;
    border-radius: 50%;
    width: 60px;
    height: 60px;
    animation: spin 1s linear infinite;
    margin-bottom: 20px;
  `;
  const style = iframeDoc.createElement("style");
  style.textContent = `
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
  `;
  iframeDoc.head.appendChild(style);
  const message = iframeDoc.createElement("div");
  message.textContent = "正在验证订单信息，请稍候...";
  message.style.cssText = "text-align: center; padding: 0 20px;";
  const subMessage = iframeDoc.createElement("div");
  subMessage.textContent = "验证地址、商品、价格等信息";
  subMessage.style.cssText = "font-size: 14px; color: #ccc; margin-top: 10px;";
  overlay.appendChild(spinner);
  overlay.appendChild(message);
  overlay.appendChild(subMessage);
  iframeDoc.body.appendChild(overlay);
}
function hideVerificationOverlay(iframeDoc) {
  const overlay = iframeDoc.getElementById("sb2-verification-overlay");
  if (overlay) {
    overlay.remove();
  }
}
async function fillAddressInfo(iframe, order, cartLayerIndex) {
  try {
    if (!iframe || !iframe.contentWindow) {
      logToOutput(`iframe 无效，无法填写地址信息`, "error");
      return false;
    }
    const iframeDoc = iframe.contentWindow.document;
    let addressSection = iframeDoc.querySelector(
      '[aria-labelledby="delivery-addresses-section-header-id"]',
    );
    if (!addressSection) {
      const changeAddressButton = iframeDoc.querySelector(
        '#checkout-deliveryAddressPanel [aria-label="Change delivery address"]',
      );
      if (!changeAddressButton) {
        logToOutput(`未找到更改地址按钮`, "error");
        return false;
      }
      changeAddressButton.click();
      const maxWaitTime = 3e4;
      const checkInterval = 500;
      let waitTime = 0;
      while (waitTime < maxWaitTime) {
        await Utils.sleep(checkInterval);
        waitTime += checkInterval;
        if (!iframe || !iframe.contentWindow) {
          logToOutput(`iframe 已失效，无法继续填写地址`, "error");
          return false;
        }
        const currentIframeDoc = iframe.contentWindow.document;
        addressSection = currentIframeDoc.querySelector(
          '[aria-labelledby="delivery-addresses-section-header-id"]',
        );
        if (addressSection) {
          break;
        }
      }
    }
    if (!addressSection) {
      logToOutput(`等待地址列表超时，跳过当前订单`, "error");
      await ApiService.updateOrderStatus(
        order.id,
        99,
        "地址列表加载超时",
        order.orderNo,
      );
      await clearShoppingCart();
      return false;
    }
    let addressButton;
    if (addressSection) {
      const addressLength = iframeDoc.querySelectorAll(
        '[aria-labelledby="delivery-addresses-section-header-id"]',
      );
      if (addressLength.length > 1) {
        const countryMapping = {
          US: "United States",
          JP: "Japan",
          CA: "Canada",
          UK: "United Kingdom",
          DE: "Germany",
          FR: "France",
          IT: "Italy",
          ES: "Spain",
          AU: "Australia",
        };
        for (let i = 1; i < addressLength.length; i++) {
          const addressContent = addressLength[i]
            .querySelectorAll("span")[1]
            .textContent.trim();
          const addressArr = addressContent.split(",");
          const countryInfo = addressArr[addressArr.length - 1].trim();
          const countryFullName = countryMapping[order.receivingCountry];
          if (countryFullName && countryInfo.includes(countryFullName)) {
            addressButton = addressLength[i].querySelector(
              "#edit-address-desktop-tango-sasp-" + i,
            );
            break;
          }
        }
        if (!addressButton) {
          addressButton = iframeDoc.querySelector(
            "#add-new-address-desktop-sasp-tango-link",
          );
        }
      } else {
        addressButton = iframeDoc.querySelector(
          "#add-new-address-desktop-sasp-tango-link",
        );
        if (addressButton) {
        } else {
          logToOutput(`未找到添加新地址按钮`, "error");
          return false;
        }
      }
    } else {
      addressButton = iframeDoc.querySelector(
        "#add-new-address-desktop-sasp-tango-link",
      );
      if (addressButton) {
      } else {
        logToOutput(`未找到添加新地址按钮`, "error");
        return false;
      }
    }
    addressButton.click();
    let nameField = null;
    let waitTime = 0;
    const maxWaitTime = 1e4;
    const checkInterval = 500;
    while (waitTime < maxWaitTime) {
      await Utils.sleep(checkInterval);
      waitTime += checkInterval;
      try {
        nameField = iframeDoc.querySelector(
          "#address-ui-widgets-enterAddressFullName",
        );
        if (nameField) {
          break;
        }
      } catch (e) {
        console.log(`等待地址表单加载时出错，继续等待...`);
      }
    }
    if (!nameField) {
      logToOutput(`地址表单加载超时，无法找到姓名字段`, "error");
      return false;
    }
    nameField.value = order.receivingName;
    nameField.dispatchEvent(new Event("input", { bubbles: true }));
    const phoneField = iframeDoc.querySelector(
      "#address-ui-widgets-enterAddressPhoneNumber",
    );
    if (phoneField) {
      phoneField.value = order.receivingPhone;
      phoneField.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      logToOutput(`未找到手机号字段`, "error");
      return false;
    }
    const address1Field = iframeDoc.querySelector(
      "#address-ui-widgets-enterAddressLine1",
    );
    if (address1Field) {
      if (order.receivingCountry == "JP") {
        if (!order.receivingDistrict || order.receivingDistrict.trim() === "") {
          logToOutput(
            `地址信息不完整，缺少区相关信息，请补充完整地址信息`,
            "error",
          );
          await ApiService.updateOrderStatus(
            order.id,
            99,
            "地址信息不完整，缺少区相关信息",
            order.orderNo,
          );
          return false;
        }
        address1Field.value = order.receivingCity + order.receivingDistrict;
      } else {
        address1Field.value = order.receivingAddress;
      }
      address1Field.dispatchEvent(new Event("input", { bubbles: true }));
    } else {
      logToOutput(`未找到地址1字段`, "error");
      return false;
    }
    const address2Field = iframeDoc.querySelector(
      "#address-ui-widgets-enterAddressLine2",
    );
    const buildingOrCompanyField = iframeDoc.querySelector(
      "#address-ui-widgets-enterBuildingOrCompanyName",
    );
    if (address2Field) {
      if (order.receivingCountry == "JP") {
        address2Field.value = order.receivingAddress;
        buildingOrCompanyField.value = "";
        buildingOrCompanyField.dispatchEvent(
          new Event("input", { bubbles: true }),
        );
      } else {
        address2Field.value = "";
      }
      address2Field.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (order.receivingCountry != "JP") {
      const cityField = iframeDoc.querySelector(
        "#address-ui-widgets-enterAddressCity",
      );
      if (cityField) {
        cityField.value = order.receivingCity;
        cityField.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        logToOutput(`未找到城市字段`, "error");
        return false;
      }
    }
    if (order.receivingCountry == "JP") {
      const postalCodeFieldOne = iframeDoc.querySelector(
        "#address-ui-widgets-enterAddressPostalCodeOne",
      );
      const postalCodeFieldTwo = iframeDoc.querySelector(
        "#address-ui-widgets-enterAddressPostalCodeTwo",
      );
      const postalCode = order.receivingPostCode.split("-");
      if (postalCodeFieldOne) {
        postalCodeFieldOne.value = postalCode[0];
        postalCodeFieldOne.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        logToOutput(`未找到邮编字段`, "error");
        return false;
      }
      if (postalCodeFieldTwo) {
        postalCodeFieldTwo.value = postalCode[1];
        postalCodeFieldTwo.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        logToOutput(`未找到邮编字段`, "error");
        return false;
      }
    } else {
      const postalCodeField = iframeDoc.querySelector(
        "#address-ui-widgets-enterAddressPostalCode",
      );
      if (postalCodeField) {
        postalCodeField.value = order.receivingPostCode;
        postalCodeField.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        logToOutput(`未找到邮编字段`, "error");
        return false;
      }
    }
    logStage("填地址-州下拉", iframeDoc);
    const stateDropdown = iframeDoc.querySelector(
      "#address-ui-widgets-enterAddressStateOrRegion-dropdown-nativeId",
    );
    if (!stateDropdown) {
      logToOutput(`未找到州下拉框`, "error");
      return false;
    }
    const stateToMatch = order.state || order.receivingState;
    if (!stateToMatch) {
      logToOutput(`订单中缺少洲/州信息`, "error");
      return false;
    }
    const optionsLength = stateDropdown.options.length;
    let stateMatched = false;
    for (let i = 0; i < optionsLength; i++) {
      const opt = stateDropdown.options[i];
      if (stateOptionMatches(stateToMatch, opt.value, opt.innerText)) {
        opt.selected = true;
        stateDropdown.dispatchEvent(new Event("change", { bubbles: true }));
        stateMatched = true;
        break;
      }
    }
    if (!stateMatched) {
      logToOutput(`未匹配到对应的州信息: ${stateToMatch}`, "error");
      await ApiService.updateOrderStatus(
        order.id,
        99,
        "未匹配到州信息，请检查订单",
        order.orderNo,
      );
      await clearShoppingCart();
      return false;
    }
    const saveAddressButton = iframeDoc.querySelector(
      "#pagelet-layout-section #checkout-primary-continue-button-id input",
    );
    if (saveAddressButton) {
      saveAddressButton.click();
      logToOutput(`检查是否需要确认地址...`);
      await Utils.sleep(2e3);
      let hasAddressConfirm = false;
      for (let i = 0; i < 6; i++) {
        await Utils.sleep(500);
        const addressConfirmElement = iframeDoc.querySelector(
          "#address-ui-widgets-enterAddressLine1-full-validation-alerts",
        );
        const address2ConfirmElement = iframeDoc.querySelector(
          "#address-ui-widgets-enterAddressLine2-full-validation-alerts",
        );
        const phoneConfirmElement = iframeDoc.querySelector(
          "#address-ui-widgets-enterAddressPhoneNumber-full-validation-alerts",
        );
        if (
          addressConfirmElement ||
          address2ConfirmElement ||
          phoneConfirmElement
        ) {
          hasAddressConfirm = true;
          logToOutput(`发现地址需要确认，开始处理...`);
          break;
        }
      }
      if (hasAddressConfirm) {
        await Utils.sleep(2e3);
        const saveAddressButton2 = iframeDoc.querySelector(
          "#pagelet-layout-section #checkout-primary-continue-button-id input",
        );
        if (saveAddressButton2) {
          saveAddressButton2.click();
          await Utils.sleep(2e3);
        } else {
          logToOutput(`未找到验证后的保存地址按钮`, "error");
          return false;
        }
      }
      logToOutput(`检查是否存在地址二次验证...`);
      let hasAddressVerification = false;
      for (let i = 0; i < 6; i++) {
        await Utils.sleep(500);
        const addressVerificationElement = iframeDoc.querySelector(
          "#address-ui-widgets-original-address-block_id-outer",
        );
        if (addressVerificationElement) {
          hasAddressVerification = true;
          logToOutput(`发现地址二次验证，开始处理...`);
          break;
        }
      }
      if (hasAddressVerification) {
        await Utils.sleep(2e3);
        const addressVerificationElement = iframeDoc.querySelector(
          "#address-ui-widgets-original-address-block_id-outer input",
        );
        if (addressVerificationElement) {
          addressVerificationElement.click();
          await Utils.sleep(1e3);
          const saveAddressButton2 = iframeDoc.querySelector(
            "#pagelet-layout-section #checkout-primary-continue-button-id input",
          );
          if (saveAddressButton2) {
            saveAddressButton2.click();
            await Utils.sleep(2e3);
          } else {
            logToOutput(`未找到验证后的保存地址按钮`, "error");
            return false;
          }
        }
      }
      const addressSaveResult = await OrderProcessor.waitForAddressSave(
        iframe,
        order,
      );
      if (!addressSaveResult) {
        logToOutput(`地址保存失败`, "error");
        return false;
      }
    } else {
      logToOutput(`未找到保存地址按钮`, "error");
      return false;
    }
    // dry_run：校验+清购物车+加购+填地址均已完成，此处即止步，不进入结账页（不分国家）
    const execMode = resolveExecMode(order);
    if (execMode === "dry_run") {
      logToOutput(`dry_run 模式：不进入结账页，标记本单校验完成`);
      const finishResult = await ApiService.updatePurchaseOrderFinish(
        order.id,
        undefined, // platformOrderNo：dry_run 未下单，无采购单号
        undefined, // asins
        undefined, // deliveryTime
        undefined, // origDeliveryTime
        undefined, // creditCardNumber
        order.orderNo,
        undefined, // mainPostCode
        undefined, // extPostCode
        undefined, // orderPriceInfo：dry_run 不带金额（ERP对缺失金额不误报）
        undefined, // extractedProducts
        execMode,
      );
      if (finishResult) {
        logToOutput(`dry_run 完成信息回传成功`);
      } else {
        logToOutput(`dry_run 完成信息回传失败`, "error");
      }
      return true;
    }
    if (order.receivingCountry === "JP") {
      const verificationResult = await verifyPageInfoJp(
        iframeDoc,
        order,
        iframe,
        cartLayerIndex,
      );
      if (!verificationResult) {
        return false;
      }
    } else {
      const verificationResult = await verifyPageInfo(
        iframeDoc,
        order,
        iframe,
        cartLayerIndex,
      );
      if (!verificationResult) {
        return false;
      }
    }
    return true;
  } catch (error) {
    logToOutput(`填写地址信息失败: ${error.message}`, "error");
    return false;
  } finally {
    window.addressFillingInProgress = false;
  }
}
async function processSingleProduct(order, asin, quantity) {
  return new Promise((resolve) => {
    try {
      const productUrl = `${window.location.origin}/dp/${asin}?th=1&psc=1`;
      const layerIndex = layer.open({
        type: 2,
        title: `商品页面 - ${asin}`,
        shade: 0.8,
        closeBtn: 1,
        area: ["1000px", "700px"],
        content: productUrl,
        success: function (layero, index) {
          setTimeout(async () => {
            try {
              const iframe = layero.find("iframe")[0];
              if (iframe && iframe.contentWindow) {
                const iframeDoc = iframe.contentWindow.document;
                const availabilitySpan = iframeDoc.querySelector(
                  "#outOfStock span.a-color-price.a-text-bold",
                );
                if (availabilitySpan) {
                  const availabilityText = availabilitySpan.textContent.trim();
                  const stockKeywords = [
                    "Currently unavailable",
                    "currently unavailable",
                  ];
                  const isInStock = stockKeywords.some((keyword) =>
                    availabilityText.includes(keyword),
                  );
                  if (isInStock) {
                    logToOutput(
                      `验证失败: 商品无库存 - ${asin}, 库存状态: ${availabilityText}`,
                      "error",
                    );
                    await ApiService.updateOrderStatus(
                      order.id,
                      99,
                      "商品无库存",
                      order.orderNo,
                    );
                    layer.close(layerIndex);
                    resolve({ success: false });
                    return;
                  }
                }
                const sel = iframeDoc.querySelector("#quantity");
                if (!sel) {
                  if (quantity == 1) {
                    logToOutput(
                      `未找到数量选择器，但购买数量为1，使用默认数量继续 - ${asin}`,
                    );
                  } else {
                    logToOutput(
                      `验证失败: 无法修改商品购买数量 - ${asin}`,
                      "error",
                    );
                    await ApiService.updateOrderStatus(
                      order.id,
                      99,
                      "无法修改商品购买数量",
                      order.orderNo,
                    );
                    layer.close(layerIndex);
                    resolve({ success: false });
                    return;
                  }
                } else {
                  const length = sel.options.length;
                  let selected = false;
                  for (let index = 0; index < length; index++) {
                    const str = sel.options[index].innerText.trim();
                    if (str == quantity) {
                      selected = true;
                      sel.options[index].selected = true;
                      const event = new Event("change", { bubbles: true });
                      sel.dispatchEvent(event);
                      break;
                    }
                  }
                  if (!selected) {
                    logToOutput(
                      `验证失败: 商品库存不足或已售罄 - ${asin}`,
                      "error",
                    );
                    await ApiService.updateOrderStatus(
                      order.id,
                      99,
                      "商品库存不足或已售罄",
                      order.orderNo,
                    );
                    layer.close(layerIndex);
                    resolve({ success: false });
                    return;
                  }
                  var one = iframeDoc.querySelector("input[id^=checkboxpctch]");
                  if (one) {
                    one.click();
                    await Utils.sleep(1e3);
                  } else {
                    var two = document.querySelector(
                      "span[id^=clickMepctch] input",
                    );
                    if (two) {
                      two.click();
                      await Utils.sleep(1e3);
                    } else {
                      logToOutput(`商品 ${asin} 暂无折扣`);
                    }
                  }
                }
                const addToCartButton = iframeDoc.querySelector(
                  "#add-to-cart-button",
                );
                if (!addToCartButton) {
                  logToOutput(
                    `验证失败: 未找到加入购物车按钮 - ${asin}`,
                    "error",
                  );
                  await ApiService.updateOrderStatus(
                    order.id,
                    99,
                    "未找到加入购物车按钮",
                    order.orderNo,
                  );
                  layer.close(layerIndex);
                  resolve({ success: false });
                  return;
                }
                addToCartButton.click();
                let hasConfirmDialog = false;
                let pageJumped = false;
                for (let i = 0; i < 60; i++) {
                  await Utils.sleep(500);
                  try {
                    const currentUrl = iframe.contentWindow.location.href;
                    if (
                      currentUrl.includes("/cart/smart-wagon") ||
                      currentUrl.includes("/gp/cart/view.html")
                    ) {
                      pageJumped = true;
                      break;
                    }
                    if (!hasConfirmDialog) {
                      const confirmDialog = iframeDoc.querySelector(
                        "#attach-warranty-pane",
                      );
                      if (confirmDialog) {
                        hasConfirmDialog = true;
                        const confirmButton = iframeDoc.querySelector(
                          "#attachSiNoCoverage input",
                        );
                        if (confirmButton) {
                          confirmButton.click();
                          await Utils.sleep(1e3);
                        } else {
                          logToOutput(`未找到确认按钮`, "error");
                        }
                      }
                    }
                  } catch (e) {
                    console.log(`检查页面状态时出错，继续等待...`);
                  }
                }
                if (!pageJumped) {
                  logToOutput(
                    `加入购物车失败: ${asin} - 页面未跳转到购物车页面`,
                    "error",
                  );
                  layer.close(layerIndex);
                  resolve({ success: false });
                  return;
                }
                logToOutput(`商品 ${asin} 成功加入购物车`);
                layer.close(layerIndex);
                resolve({ success: true });
              }
            } catch (error) {
              logToOutput(`处理商品失败: ${asin} - ${error.message}`, "error");
              await ApiService.updateOrderStatus(
                order.id,
                99,
                `处理商品失败: ${error.message}`,
                order.orderNo,
              );
              layer.close(layerIndex);
              resolve({ success: false });
            }
          }, 2e3);
        },
        end: function () {
          logToOutput(`商品页面已关闭: ${asin}`);
        },
      });
    } catch (error) {
      logToOutput(`处理商品异常: ${asin} - ${error.message}`);
      resolve({ success: false });
    }
  });
}
async function processCartAndCheckout(order, addedProducts) {
  return new Promise((resolve) => {
    try {
      const cartUrl = `${window.location.origin}/gp/cart/view.html`;
      const cartLayerIndex = layer.open({
        type: 2,
        title: `购物车核实 - 订单 ${order.orderNo}`,
        shade: 0.8,
        closeBtn: 1,
        area: ["1000px", "700px"],
        content: cartUrl,
        success: function (layero, index) {
          setTimeout(async () => {
            try {
              const iframe = layero.find("iframe")[0];
              if (iframe && iframe.contentWindow) {
                const iframeDoc = iframe.contentWindow.document;
                if (window.addressFillingInProgress) {
                  return;
                }
                logToOutput("开始核实购物车中的商品信息...");
                const cartItems = iframeDoc.querySelectorAll(
                  '[data-name="Active Items"] .sc-list-item',
                );
                if (cartItems.length != order.products.length) {
                  logToOutput(
                    `购物车中商品数量与订单中商品数量不匹配: 购物车${cartItems.length}, 订单${order.products.length}，跳过当前订单，等待下一次执行`,
                    "error",
                  );
                  await OrderProcessor.clearCart(iframeDoc);
                  await Utils.sleep(2e3);
                  layer.close(cartLayerIndex);
                  resolve();
                  return;
                }
                let verifiedCount = 0;
                let orderVerificationFailed = false;
                for (const addedProduct of order.products) {
                  const asin = addedProduct.asin;
                  const expectedQuantity = addedProduct.quantity;
                  const cartItem = Array.from(cartItems).find(
                    (item) => item.getAttribute("data-asin") === asin,
                  );
                  if (cartItem) {
                    let quantitySelect = cartItem.querySelector(
                      '[data-a-selector="value"]',
                    );
                    let selectedQuantity = null;
                    if (quantitySelect) {
                      selectedQuantity = parseInt(
                        quantitySelect.textContent.trim(),
                        10,
                      );
                    } else {
                      const nonEditableQuantity = cartItem.querySelector(
                        ".sc-non-editable-quantity",
                      );
                      if (nonEditableQuantity) {
                        const quantityText = nonEditableQuantity.textContent;
                        const quantityMatch = quantityText.match(/\d+/);
                        if (quantityMatch) {
                          selectedQuantity = parseInt(quantityMatch[0], 10);
                        }
                      }
                    }
                    if (selectedQuantity !== null && !isNaN(selectedQuantity)) {
                      if (selectedQuantity == expectedQuantity) {
                        verifiedCount++;
                      } else {
                        logToOutput(
                          `商品 ${asin} 数量不匹配: 期望${expectedQuantity}, 实际${selectedQuantity}`,
                          "error",
                        );
                        orderVerificationFailed = true;
                        break;
                      }
                    } else {
                      logToOutput(`商品 ${asin} 未找到数量选择器`, "error");
                      orderVerificationFailed = true;
                      break;
                    }
                  } else {
                    logToOutput(`商品 ${asin} 未在购物车中找到`, "error");
                    await ApiService.updateOrderStatus(
                      order.id,
                      99,
                      `商品 ${asin} 验证失败，请确认商品状态或库存是否充足`,
                      order.orderNo,
                    );
                    orderVerificationFailed = true;
                    break;
                  }
                }
                if (orderVerificationFailed) {
                  logToOutput(
                    `订单 ${order.orderNo} 商品核实失败，清空购物车并跳过当前订单`,
                    "error",
                  );
                  await OrderProcessor.clearCart(iframeDoc);
                  await Utils.sleep(2e3);
                  layer.close(cartLayerIndex);
                  resolve();
                  return;
                }
                window.addressFillingInProgress = true;
                logToOutput(`开始结算操作...`);
                await Utils.sleep(1e3);
                const checkoutButton = iframeDoc.querySelector(
                  "#sc-buy-box-ptc-button > span > input",
                );
                if (checkoutButton) {
                  checkoutButton.click();
                  const navigationResult = await Utils.waitForPageNavigation(
                    iframe,
                    ["checkout/byg/ref", "/cart/byc/ref", "/checkout/p/p-"],
                    3e4,
                    500,
                  );
                  if (!navigationResult.success) {
                    logToOutput(
                      `页面跳转超时或失败: ${navigationResult.reason}`,
                      "error",
                    );
                    resolve();
                    return;
                  }
                  try {
                    const currentUrl = navigationResult.url;
                    if (
                      currentUrl.includes("checkout/byg/ref") ||
                      currentUrl.includes("/cart/byc/ref")
                    ) {
                      const iframeDoc2 = iframe.contentWindow.document;
                      let finalCheckoutButton = iframeDoc2.querySelectorAll(
                        "#checkout-byg-ptc-button a",
                      );
                      if (currentUrl.includes("/cart/byc/ref")) {
                        finalCheckoutButton = iframeDoc2.querySelectorAll(
                          "#sc-byc-ptc-button-lower a",
                        );
                      }
                      if (finalCheckoutButton.length > 0) {
                        logToOutput(`找到结算按钮，点击尝试跳转`);
                        finalCheckoutButton[0].click();
                      } else {
                        logToOutput(`未找到结算按钮，等待自动跳转`);
                      }
                      const finalNavigationResult =
                        await Utils.waitForPageNavigation(
                          iframe,
                          ["/checkout/p/p-"],
                          3e4,
                          500,
                        );
                      if (!finalNavigationResult.success) {
                        logToOutput(
                          `最终结算页面跳转超时或失败: ${finalNavigationResult.reason}`,
                          "error",
                        );
                        resolve();
                        return;
                      }
                      const finalUrl = finalNavigationResult.url;
                      if (finalUrl.includes("/checkout/p/p-")) {
                        const addressResult = await fillAddressInfo(
                          iframe,
                          order,
                          cartLayerIndex,
                        );
                        if (addressResult) {
                          logToOutput(`地址填写和下单完成`);
                        }
                        resolve();
                      } else {
                        logToOutput(
                          `未成功跳转到最终结算页面，当前URL: ${finalUrl}`,
                          "error",
                        );
                        resolve();
                      }
                    } else if (currentUrl.includes("/checkout/p/p-")) {
                      const addressResult = await fillAddressInfo(
                        iframe,
                        order,
                        cartLayerIndex,
                      );
                      if (addressResult) {
                        logToOutput(`地址填写和下单完成`);
                      }
                      resolve();
                    } else {
                      logToOutput(
                        `页面跳转异常，当前URL: ${currentUrl}`,
                        "error",
                      );
                      resolve();
                    }
                  } catch (e) {
                    logToOutput(`检查页面跳转时出错: ${e.message}`, "error");
                  }
                } else {
                  logToOutput(`未找到结算按钮`, "error");
                  resolve();
                }
              }
            } catch (error) {
              logToOutput(`购物车核实失败: ${error.message}`, "error");
              layer.close(cartLayerIndex);
              resolve();
            }
          }, 3e3);
        },
        end: function () {
          logToOutput("购物车页面已关闭");
        },
      });
    } catch (error) {
      logToOutput(`处理购物车和下单异常: ${error.message}`, "error");
      resolve();
    }
  });
}
async function clearShoppingCart() {
  return new Promise((resolve, reject) => {
    try {
      const cartUrl = `${window.location.origin}/gp/cart/view.html`;
      const cartLayerIndex = layer.open({
        type: 2,
        title: "清空购物车",
        shade: 0.8,
        closeBtn: 1,
        area: ["1000px", "700px"],
        content: cartUrl,
        success: function (layero, index) {
          setTimeout(async () => {
            try {
              const iframe = layero.find("iframe")[0];
              if (iframe && iframe.contentWindow) {
                const iframeDoc = iframe.contentWindow.document;
                await OrderProcessor.clearCart(iframeDoc);
                logToOutput(`购物车清空完成`);
                await Utils.sleep(2e3);
                layer.close(cartLayerIndex);
              }
            } catch (error) {
              logToOutput(`清空购物车失败: ${error.message}`, "error");
              layer.close(cartLayerIndex);
              reject(error);
            }
          }, 2e3);
        },
        end: function () {
          logToOutput("购物车页面已关闭");
          resolve();
        },
      });
    } catch (error) {
      logToOutput(`清空购物车异常: ${error.message}`, "error");
      reject(error);
    }
  });
}
AppController.setupEventListeners();
async function processOrder(
  orderNumber,
  orderId,
  platformTrackUrl,
  mainPostCode,
  card,
  logisticsType,
  country,
) {
  let trackingInfo = null;
  let orderInfo = null;
  const baseUrl = window.location.origin;
  let layerIndex = null;
  try {
    const searchUrl = `${baseUrl}/gp/your-account/order-details?ie=UTF8&orderID=${encodeURIComponent(orderNumber)}`;
    let iframeElement = null;
    layerIndex = layer.open({
      type: 2,
      title: !1,
      shade: 0.8,
      closeBtn: 0,
      area: ["1000px", "700px"],
      content: searchUrl,
      success: function (layero, index) {
        iframeElement = layero.find("iframe")[0];
      },
    });
    const { found } = await new Promise((resolve) => {
      let isLoaded = false;
      let checkInterval = null;
      const cleanup = () => {
        if (checkInterval) {
          clearInterval(checkInterval);
          checkInterval = null;
        }
      };
      checkInterval = setInterval(() => {
        if (!iframeElement) return;
        const currentUrl = iframeElement.contentWindow.location.href;
        if (!currentUrl.includes(`${baseUrl}/gp/your-account/order-details`)) {
          cleanup();
          resolve({ found: false });
        }
        const iframe = layer.getChildFrame("body", layerIndex);
        if (iframe.length > 0) {
          const ordersContainer = iframe.find("#orderDetails");
          if (ordersContainer.length > 0) {
            cleanup();
            isLoaded = true;
            resolve({ found: true });
          } else {
            isLoaded = false;
            resolve({ found: false });
          }
        }
      }, 500);
    });
    if (!found) {
      return;
    }
    const iframe = layer.getChildFrame("body", layerIndex);
    const ordersContainer = iframe.find("#orderDetails");
    const orderStatus = ordersContainer.find(".a-alert-heading").text().trim();
    if (orderStatus && orderStatus.toLowerCase().includes("cancelled")) {
      logToOutput(`订单 ${orderNumber} 已取消`, "error");
      await ApiService.updateAmzOrderStatus(orderId, orderNumber, 91);
      return;
    }
    const returnOrder = ordersContainer.find("#shipment-top-row").text().trim();
    if (returnOrder && returnOrder.toLowerCase().includes("refund")) {
      logToOutput(`订单 ${orderNumber} 已退货`, "error");
      await ApiService.updateAmzOrderStatus(orderId, orderNumber, 91);
      return;
    }
    if (
      orderStatus &&
      orderStatus.toLowerCase().includes("unable to load your order details")
    ) {
      logToOutput(`订单 ${orderNumber} 不存在，请检查订单号是否正确`, "error");
      await ApiService.updateAmzOrderStatus(orderId, orderNumber, 92);
      return;
    }
    orderInfo = await extractOrderInfo(ordersContainer, country);
    trackingInfo = await extractTrackingInfo(ordersContainer, platformTrackUrl);
    await ApiService.updateOrderTrackingInfo(
      orderId,
      trackingInfo,
      orderNumber,
      orderInfo,
    );
    return trackingInfo;
  } catch (error) {
    console.error(`处理订单 ${orderNumber} 时出错:`, error);
    logToOutput(`处理订单 ${orderNumber} 时出错: ${error.message}`);
    throw error;
  } finally {
    if (layerIndex !== null) {
      layer.close(layerIndex);
    }
  }
}
async function processOrderTracking(
  orderNumber,
  orderId,
  platformTrackUrl,
  mainPostCode,
  card,
  logisticsType,
  country,
) {
  let trackingInfo = null;
  let orderInfo = null;
  const baseUrl = window.location.origin;
  let layerIndex = null;
  try {
    if (platformTrackUrl) {
      orderInfo = { mainPostCode: mainPostCode, card: card };
      trackingInfo =
        await extractTrackingInfoByPlatformTrackUrl(platformTrackUrl);
    } else {
      const searchUrl = `${baseUrl}/gp/your-account/order-details?ie=UTF8&orderID=${encodeURIComponent(orderNumber)}`;
      let iframeElement = null;
      layerIndex = layer.open({
        type: 2,
        title: !1,
        shade: 0.8,
        closeBtn: 0,
        area: ["1000px", "700px"],
        content: searchUrl,
        success: function (layero, index) {
          iframeElement = layero.find("iframe")[0];
        },
      });
      const { found } = await new Promise((resolve) => {
        let isLoaded = false;
        let checkInterval = null;
        const cleanup = () => {
          if (checkInterval) {
            clearInterval(checkInterval);
            checkInterval = null;
          }
        };
        checkInterval = setInterval(() => {
          if (!iframeElement) return;
          const currentUrl = iframeElement.contentWindow.location.href;
          if (
            !currentUrl.includes(`${baseUrl}/gp/your-account/order-details`)
          ) {
            cleanup();
            resolve({ found: false });
          }
          const iframe = layer.getChildFrame("body", layerIndex);
          if (iframe.length > 0) {
            const ordersContainer = iframe.find("#orderDetails");
            if (ordersContainer.length > 0) {
              cleanup();
              isLoaded = true;
              resolve({ found: true });
            } else {
              isLoaded = false;
              resolve({ found: false });
            }
          }
        }, 500);
      });
      if (!found) {
        return;
      }
      const iframe = layer.getChildFrame("body", layerIndex);
      const ordersContainer = iframe.find("#orderDetails");
      const orderStatus = ordersContainer
        .find(".a-alert-heading")
        .text()
        .trim();
      if (orderStatus && orderStatus.toLowerCase().includes("cancelled")) {
        logToOutput(`订单 ${orderNumber} 已取消`);
        await ApiService.updateAmzOrderStatus(orderId, orderNumber, 91);
        return;
      }
      const returnOrder = ordersContainer
        .find("#shipment-top-row")
        .text()
        .trim();
      if (returnOrder && returnOrder.toLowerCase().includes("refund")) {
        logToOutput(`订单 ${orderNumber} 已退货`, "error");
        await ApiService.updateAmzOrderStatus(orderId, orderNumber, 91);
        return;
      }
      orderInfo = await extractOrderInfo(ordersContainer);
      trackingInfo = await extractTrackingInfo(ordersContainer);
    }
    if (trackingInfo) {
      if (
        trackingInfo.events &&
        trackingInfo.events.length <= 1 &&
        logisticsType !== 3
      ) {
        logToOutput(
          `订单 ${orderNumber} 只有 ${trackingInfo.events.length} 条物流轨迹，跳过同步`,
        );
        return;
      }
      logToOutput(`获取到订单 ${orderNumber} 的跟踪信息，准备更新到服务器`);
      await ApiService.updateOrderTrackingInfoPig(
        orderId,
        trackingInfo,
        orderNumber,
        orderInfo,
      );
    } else {
      logToOutput(`未获取到订单 ${orderNumber} 的跟踪信息`);
    }
    return trackingInfo;
  } catch (error) {
    console.error(`处理订单 ${orderNumber} 时出错:`, error);
    logToOutput(`处理订单 ${orderNumber} 时出错: ${error.message}`);
    throw error;
  } finally {
    if (layerIndex !== null) {
      layer.close(layerIndex);
    }
  }
}
async function extractOrderInfo(ordersContainer, country) {
  const orderInfo = {
    mainPostCode: "",
    extPostCode: "",
    card: "",
    orderDate: "",
    subtotal: "",
    shipping: "",
    totalBeforeTax: "",
    tax: "",
    total: "",
    paymentTotal: "",
    asins: [],
  };
  const orderInfoElement = ordersContainer.find(
    ".a-column.a-span9.a-spacing-top-mini span",
  );
  if (orderInfoElement.length > 0) {
    const orderInfoText = orderInfoElement.text().trim();
    let dateMatch = orderInfoText.match(
      /(?:Order placed|注文日)\s*([A-Za-z]+ \d+, \d{4}|\d{4}\/\d{1,2}\/\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日)/,
    );
    if (dateMatch) {
      orderInfo.orderDate = dateMatch[1];
    } else {
      dateMatch = orderInfoText.match(
        /(?:Order placed|注文日)([A-Za-z]+ \d+, \d{4}|\d{4}\/\d{1,2}\/\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日)/,
      );
      if (dateMatch) {
        orderInfo.orderDate = dateMatch[1];
      }
    }
  }
  const priceElements = ordersContainer.find(
    "#od-subtotals .a-row.od-line-item-row",
  );
  priceElements.each(function () {
    const divs = $(this).find("div");
    if (divs.length >= 2) {
      const label = divs
        .eq(0)
        .text()
        .replace(/\s+/g, " ")
        .replace(/[:：]/g, "")
        .trim()
        .toLowerCase();
      const value = divs.eq(1).text().trim();
      if (label === "shipping & handling" || label === "配送料・手数料") {
        orderInfo.shipping = value;
      } else if (label === "total before tax" || label === "item(s) subtotal") {
        orderInfo.totalBeforeTax = value;
      } else if (
        label === "estimated tax to be collected" ||
        label === "estimated gst/hst"
      ) {
        orderInfo.tax = value;
      } else if (label === "grand total" || label === "ご請求額") {
        orderInfo.total = value;
      } else if (label === "payment grand total") {
        orderInfo.paymentTotal = value;
      }
    }
  });
  if (orderInfo.paymentTotal !== "") {
    orderInfo.total = orderInfo.total + "\n(" + orderInfo.paymentTotal + ")";
  }
  const productLinks = ordersContainer.find(
    ".a-fixed-left-grid-col.a-col-right .a-row .a-link-normal",
  );
  const asinList = [];
  productLinks.each(function () {
    const href = $(this).attr("href");
    if (href) {
      const asinMatch = href.match(/\b(B0\w{8}|\d{10}|\d{9}X)\b/);
      if (asinMatch && asinMatch[1]) {
        asinList.push(asinMatch[1]);
      }
    }
  });
  orderInfo.asins = asinList;
  let card = ordersContainer.find(".pmts-payments-instrument-details");
  if (card.length > 0) {
    const cardText = card.text().trim();
    const matches = cardText.match(/(?:ending\s+in|[*]{4}|下4桁)\s*(\d{4})/i);
    if (matches && matches[1]) {
      orderInfo.card = matches[1];
    }
  } else {
    card = ordersContainer.find(
      '[data-component="viewPaymentPlanSummaryWidget"] > div [data-testid="method-details-number"]',
    );
    if (card.length > 0) {
      orderInfo.card = card.text().trim();
    }
  }
  const cityStateZip = ordersContainer.find(
    ".displayAddressLI.displayAddressCityStateOrRegionPostalCode",
  );
  let postalCodeInfo = null;
  if (cityStateZip.length > 0) {
    postalCodeInfo = cityStateZip.text().trim();
  } else {
    const shippingAddress = ordersContainer.find(
      '[data-component="shippingAddress"] li:nth-child(2) span',
    );
    if (shippingAddress.length > 0) {
      const addressHtml = shippingAddress.html();
      if (addressHtml) {
        const addressParts = addressHtml.split("<br>");
        if (country === "JP") {
          if (addressParts.length > 3) {
            postalCodeInfo = addressParts[3].trim();
          } else {
            postalCodeInfo = addressParts[2].trim();
          }
        } else {
          if (addressParts.length > 2) {
            postalCodeInfo = addressParts[2].trim();
          } else {
            postalCodeInfo = addressParts[1].trim();
          }
        }
      }
    }
  }
  const matches = postalCodeInfo.match(
    /^(?:([^,]+),\s*)?([^,]+?)\s+(?:(\d{5})(?:-(\d{4}))?|([A-Za-z]\d[A-Za-z]\s\d[A-Za-z]\d)|(\d{3}-\d{4}))$/,
  );
  if (matches) {
    if (matches[3]) {
      orderInfo.mainPostCode = matches[3];
      orderInfo.extPostCode = matches[4] || "";
    } else if (matches[5]) {
      orderInfo.mainPostCode = matches[5];
      orderInfo.extPostCode = "";
    } else if (matches[6]) {
      orderInfo.mainPostCode = matches[6];
      orderInfo.extPostCode = "";
    }
  }
  return orderInfo;
}
async function extractTrackingInfoByPlatformTrackUrl(trackingLink) {
  try {
    const trackingParams = extractUrlParams(trackingLink);
    let trackingLayerIndex = null;
    try {
      trackingLayerIndex = layer.open({
        type: 2,
        title: !1,
        shade: 0.8,
        closeBtn: 0,
        area: ["1000px", "700px"],
        content: trackingLink,
      });
      const trackingInfo = await new Promise((resolve) => {
        let isLoaded = false;
        let checkInterval = null;
        const cleanup = () => {
          if (checkInterval) {
            clearInterval(checkInterval);
            checkInterval = null;
          }
        };
        checkInterval = setInterval(() => {
          const trackingIframe = layer.getChildFrame(
            "html",
            trackingLayerIndex,
          );
          let trackingNumber = null;
          let trackingElement = trackingIframe.find(
            ".pt-delivery-card-trackingId",
          );
          if (trackingElement.length > 0) {
            cleanup();
            isLoaded = true;
            const trackingText = trackingElement.text().trim();
            const trackingMatch = trackingText.match(/Tracking ID:\s*(\w+)/);
            if (trackingMatch) {
              trackingNumber = trackingMatch[1];
            }
          } else {
            trackingElement = trackingIframe.find(
              "#carrierRelatedInfo-container > div h4",
            );
            if (trackingElement.length > 0) {
              cleanup();
              isLoaded = true;
              const trackingText = trackingElement.text().trim();
              const trackingMatch = trackingText.match(/Tracking ID:\s*(\w+)/);
              if (trackingMatch) {
                trackingNumber = trackingMatch[1];
              }
            }
          }
          const currentContent = trackingIframe.prop("outerHTML");
          if (
            currentContent &&
            currentContent.length > 50 &&
            !currentContent.match(/<html><head><\/head><body><\/body><\/html>/)
          ) {
            cleanup();
            isLoaded = true;
            const fullHTML = getCompleteHTML(trackingIframe[0].contentWindow);
            const events = extractTrackingEvents(fullHTML || currentContent);
            resolve({
              trackingNumber: trackingNumber,
              url: trackingLink,
              html: "<!doctype html>\n" + fullHTML,
              params: trackingParams,
              events: events,
            });
          } else {
            console.log("检测到空内容，等待加载...");
            if (!window.contentWaitTimeout) {
              window.contentWaitTimeout = setTimeout(() => {
                window.contentWaitTimeout = null;
                const updatedContent = layer
                  .getChildFrame("html", trackingLayerIndex)
                  .prop("outerHTML");
                console.log(
                  "延迟后获取内容:",
                  updatedContent.substring(0, 100) + "...",
                );
                cleanup();
                isLoaded = true;
                const events = extractTrackingEvents(updatedContent);
                const updatedHTML = updateEventsTimeInHTML(
                  updatedContent,
                  events,
                );
                resolve({
                  trackingNumber: trackingNumber,
                  url: trackingLink,
                  html: "<!doctype html>\n" + updatedHTML || "内容为空",
                  params: trackingParams,
                  events: events,
                });
              }, 5e3);
            }
          }
        }, 5e3);
        setTimeout(() => {
          if (!isLoaded) {
            cleanup();
            if (window.contentWaitTimeout) {
              clearTimeout(window.contentWaitTimeout);
              window.contentWaitTimeout = null;
            }
            resolve(null);
          }
        }, 5e3);
      });
      return trackingInfo;
    } finally {
      if (trackingLayerIndex !== null) {
        layer.close(trackingLayerIndex);
      }
    }
  } catch (error) {
    console.error("提取跟踪信息时出错:", error);
  }
}
async function extractTrackingInfo(ordersContainer, platformTrackUrl) {
  try {
    const trackingHrefs = [];
    if (platformTrackUrl) {
      trackingHrefs.push(platformTrackUrl);
    } else {
      const trackingLinks = ordersContainer.find(
        ".a-button-stack.a-spacing-mini a",
      );
      trackingLinks.each(function () {
        const href = $(this).attr("href");
        if (href && href.includes("/ship-track?")) {
          trackingHrefs.push(href);
        }
      });
      const primaryTrackButtons = ordersContainer.find(
        ".a-button.a-button-primary.track-package-button a",
      );
      primaryTrackButtons.each(function () {
        const href = $(this).attr("href");
        if (
          href &&
          href.includes(
            "/progress-tracker/package/ref=ppx_od_dt_b_track_package?",
          )
        ) {
          trackingHrefs.push(href);
        }
      });
      const baseTrackButtons = ordersContainer.find(
        ".a-button.a-button-base.track-package-button a",
      );
      baseTrackButtons.each(function () {
        const href = $(this).attr("href");
        if (
          href &&
          href.includes(
            "/progress-tracker/package/ref=ppx_od_dt_b_track_package?",
          )
        ) {
          trackingHrefs.push(href);
        }
      });
    }
    const uniqueTrackingHrefs = [...new Set(trackingHrefs)];
    if (uniqueTrackingHrefs.length === 0) {
      return;
    }
    const baseUrl = window.location.origin;
    if (uniqueTrackingHrefs.length > 0) {
      const trackingHref = uniqueTrackingHrefs[0];
      const trackingParams = extractUrlParams(
        trackingHref.startsWith("http") ? trackingHref : baseUrl + trackingHref,
      );
      let trackingLayerIndex = null;
      try {
        const trackingLink = trackingHref.startsWith("http")
          ? trackingHref
          : baseUrl + trackingHref;
        trackingLayerIndex = layer.open({
          type: 2,
          title: !1,
          shade: 0.8,
          closeBtn: 0,
          area: ["1000px", "700px"],
          content: trackingLink,
        });
        const trackingInfo = await new Promise((resolve) => {
          let isLoaded = false;
          let checkInterval = null;
          let maxWaitTime = 3e5;
          let startTime = Date.now();
          let consecutiveEmptyChecks = 0;
          const maxEmptyChecks = 10;
          const cleanup = () => {
            if (checkInterval) {
              clearInterval(checkInterval);
              checkInterval = null;
            }
          };
          const checkPageLoaded = () => {
            const trackingIframe = layer.getChildFrame(
              "html",
              trackingLayerIndex,
            );
            const currentContent = trackingIframe.prop("outerHTML");
            if (Date.now() - startTime > maxWaitTime) {
              console.log("页面加载超时，停止等待");
              cleanup();
              isLoaded = true;
              resolve(null);
              return;
            }
            if (
              currentContent &&
              currentContent.length > 50 &&
              !currentContent.match(
                /<html><head><\/head><body><\/body><\/html>/,
              )
            ) {
              const hasTrackingContent =
                trackingIframe.find(
                  ".pt-delivery-card-trackingId, #carrierRelatedInfo-container > div h4",
                ).length > 0;
              const hasDeliveryContent =
                trackingIframe.find(
                  ".pt-delivery-card-wrapper, .pt-promise-main-slot, #primaryStatus",
                ).length > 0;
              if (hasTrackingContent || hasDeliveryContent) {
                console.log("检测到有效内容，页面加载完成");
                cleanup();
                isLoaded = true;
                let trackingNumber = null;
                let carrier = null;
                let deliveryImg = null;
                let deliveryDate = null;
                let trackingElement = trackingIframe.find(
                  ".pt-delivery-card-trackingId",
                );
                if (trackingElement.length > 0) {
                  const trackingText = trackingElement.text().trim();
                  const trackingMatch = trackingText.match(
                    /Tracking ID:?\s*(\w+)/,
                  );
                  if (trackingMatch) {
                    trackingNumber = trackingMatch[1];
                  }
                } else {
                  trackingElement = trackingIframe.find(
                    "#carrierRelatedInfo-container > div h4",
                  );
                  if (trackingElement.length > 0) {
                    const trackingText = trackingElement.text().trim();
                    const trackingMatch =
                      trackingText.match(/Tracking ID:\s*(\w+)/);
                    if (trackingMatch) {
                      trackingNumber = trackingMatch[1];
                    }
                  }
                }
                let carrierElement = trackingIframe.find(
                  ".pt-delivery-card-wrapper .a-spacing-small",
                );
                if (carrierElement.length > 0) {
                  const carrierText = carrierElement.text().trim();
                  const carrierMatch = carrierText.split(" ");
                  if (carrierMatch) {
                    carrier = carrierMatch[2];
                  }
                }
                let deliveryDateElement = trackingIframe.find(
                  ".pt-promise-main-slot .nowrap",
                );
                if (deliveryDateElement.length > 0) {
                  deliveryDate = deliveryDateElement.text().trim();
                } else {
                  deliveryDateElement = trackingIframe.find(
                    ".pt-promise-main-slot",
                  );
                  deliveryDate = deliveryDateElement.text().trim();
                }
                let deliveryImgElement = trackingIframe.find(
                  ".pt-delivery-card-wrapper .a-declarative img",
                );
                if (deliveryImgElement.length > 0) {
                  const deliveryImgSrc = deliveryImgElement.attr("src");
                  if (deliveryImgSrc) {
                    deliveryImg = deliveryImgSrc;
                  }
                }
                const fullHTML = getCompleteHTML(
                  trackingIframe[0].contentWindow,
                );
                const events = extractTrackingEvents(
                  fullHTML || currentContent,
                );
                resolve({
                  carrier: carrier,
                  trackingNumber: trackingNumber,
                  deliveryImg: deliveryImg,
                  deliveryDate: deliveryDate,
                  url: trackingLink,
                  html: "<!doctype html>\n" + fullHTML,
                  params: trackingParams,
                  events: events,
                });
                return;
              }
            }
            if (
              !currentContent ||
              currentContent.length <= 50 ||
              currentContent.match(/<html><head><\/head><body><\/body><\/html>/)
            ) {
              consecutiveEmptyChecks++;
              console.log(
                `检测到空内容，等待加载... (${consecutiveEmptyChecks}/${maxEmptyChecks})`,
              );
              if (consecutiveEmptyChecks >= maxEmptyChecks) {
                console.log("连续检测到空内容次数过多，可能页面加载有问题");
                cleanup();
                isLoaded = true;
                resolve(null);
                return;
              }
            } else {
              consecutiveEmptyChecks = 0;
            }
          };
          checkPageLoaded();
          checkInterval = setInterval(checkPageLoaded, 2e3);
        });
        return trackingInfo;
      } finally {
        if (trackingLayerIndex !== null) {
          layer.close(trackingLayerIndex);
        }
      }
    }
  } catch (error) {
    console.error("提取跟踪信息时出错:", error);
  }
}
function clearLogOutput() {
  const logContent = document.querySelector(".sb2-log-content");
  if (logContent) {
    logContent.innerHTML = "";
  }
}
function compressHTML(html) {
  return html
    .replace(/\s+/g, " ")
    .replace(/>\s+</g, "><")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/\s+$/gm, "")
    .trim();
}
function getCompleteHTML(windowObj) {
  try {
    if (!windowObj || !windowObj.document) return null;
    const doc = windowObj.document;
    let html = doc.documentElement.outerHTML;
    const styleSheets = Array.from(doc.styleSheets);
    let inlineStyles = "";
    styleSheets.forEach((sheet) => {
      try {
        if (sheet.cssRules) {
          const rules = Array.from(sheet.cssRules);
          rules.forEach((rule) => {
            inlineStyles += rule.cssText + "\n";
          });
        }
      } catch (e) {
        console.log("无法访问样式表规则（可能是跨域限制）:", e);
      }
    });
    if (inlineStyles) {
      html = html.replace(
        "<head>",
        `<head><style type="text/css">/* 自动收集的样式 */\n${inlineStyles}</style>`,
      );
    }
    return html;
  } catch (error) {
    console.error("获取完整HTML时出错:", error);
    return null;
  }
}
function extractUrlParams(url) {
  try {
    const urlObj = new URL(url);
    const params = {};
    urlObj.searchParams.forEach((value, key) => {
      params[key] = value;
    });
    return params;
  } catch (error) {
    console.error("解析URL参数时出错:", error);
    return {};
  }
}
function extractTrackingEvents(html) {
  try {
    if (!html) return [];
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const container = doc.querySelector(
      "#tracking-events-container > div.a-container",
    );
    if (!container) {
      console.log("未找到跟踪事件容器");
      return [];
    }
    const trackingIdElement = container.querySelector(
      ".tracking-event-trackingId-text h4",
    );
    const trackingId = trackingIdElement
      ? trackingIdElement.textContent.trim().replace("Tracking ID:", "").trim()
      : "";
    console.log(`找到跟踪号: ${trackingId}`);
    const eventRows = container.querySelectorAll(
      ".a-row.a-spacing-large.a-spacing-top-medium",
    );
    console.log(`找到 ${eventRows.length} 个事件行`);
    const events = [];
    eventRows.forEach((row) => {
      try {
        const timeElement = row.querySelector(".tracking-event-time");
        const time = timeElement ? timeElement.textContent.trim() : "";
        const messageElement = row.querySelector(".tracking-event-message");
        const message = messageElement ? messageElement.textContent.trim() : "";
        const locationElement = row.querySelector(".tracking-event-location");
        const location = locationElement
          ? locationElement.textContent.trim()
          : "";
        const locationState = location
          ? (() => {
              let parts = [];
              if (location.split(",").length > 1) {
                parts = location.split(",")[1].trim().split(" ");
              }
              return parts.length > 1
                ? parts.slice(0, -1).join(" ")
                : parts[0] || "";
            })()
          : "";
        const locationCity = location ? location.split(",")[0].trim() : "";
        let date = "";
        let dateHeader = row.previousElementSibling;
        while (dateHeader) {
          if (dateHeader.classList.contains("tracking-event-date-header")) {
            const dateElement = dateHeader.querySelector(
              ".tracking-event-date",
            );
            date = dateElement ? dateElement.textContent.trim() : "";
            break;
          }
          dateHeader = dateHeader.previousElementSibling;
        }
        if (message) {
          events.push({
            day: date,
            time: time,
            tracking_info: message,
            state: locationState,
            city: locationCity,
          });
        }
      } catch (err) {
        console.error("处理事件行时出错:", err);
      }
    });
    if (events.length === 0) return [];
    return events;
  } catch (error) {
    console.error("提取跟踪事件时出错:", error);
    return [];
  }
}
async function retryRequest(requestFn, maxRetries = 3, delay = 1e3) {
  let lastError;
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      return await requestFn();
    } catch (error) {
      lastError = error;
      if (attempt < maxRetries) {
        logToOutput(
          `请求失败，第 ${attempt} 次重试... (${error.message})`,
          "error",
        );
        await new Promise((resolve) => setTimeout(resolve, delay));
        delay *= 2;
      }
    }
  }
  throw lastError;
}
function updateEventsTimeInHTML(html, events) {
  if (!html || !events || events.length === 0) return html;
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const container = doc.querySelector(
      "#tracking-events-container > div.a-container",
    );
    if (!container) return html;
    const eventRows = container.querySelectorAll(
      ".a-row.a-spacing-large.a-spacing-top-medium",
    );
    if (
      events.length > 0 &&
      events[events.length - 1].time &&
      eventRows.length > 0
    ) {
      const firstRow = eventRows[eventRows.length - 1];
      const timeElement = firstRow.querySelector(".tracking-event-time");
      if (timeElement) {
        console.log(`更新HTML中的事件时间: ${events[events.length - 1].time}`);
        timeElement.textContent = events[events.length - 1].time;
      }
    }
    return doc.documentElement.outerHTML;
  } catch (error) {
    console.error("更新HTML中的事件时间时出错:", error);
    return html;
  }
}
