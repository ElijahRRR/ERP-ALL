"""模拟采集 worker（R1 验收用；真实采集引擎随 R2 workers/ 交付）。

走真实拨入协议（register → pull → result），给每个待采任务回传一份干净的
演示 payload——用于在没有真 worker 的环境把"采集→产品入库"这一跳打通。

用法（部署机，api 容器内执行）：
  docker compose -f infra/docker-compose.yml exec \\
    -e ERP_SIM_ENROLL_TOKEN="<注册令牌>" api python -m erp.tools.sim_worker

环境变量：
  ERP_SIM_ENROLL_TOKEN  必填，= system_config scrape.worker_enroll_token
  ERP_SIM_API_BASE      默认 http://localhost:8000/api/v1（容器内=本机）
  ERP_SIM_WPT           默认 Drinkware（写入 attrs.wpt 供上架 spec 用）
"""

import os
import sys
import time

import httpx


def main() -> int:
    enroll = os.environ.get("ERP_SIM_ENROLL_TOKEN", "")
    if not enroll:
        print("缺少 ERP_SIM_ENROLL_TOKEN（= system_config scrape.worker_enroll_token）")
        return 1
    base = os.environ.get("ERP_SIM_API_BASE", "http://localhost:8000/api/v1")
    wpt = os.environ.get("ERP_SIM_WPT", "Drinkware")

    with httpx.Client(base_url=base, timeout=30) as client:
        node_key = f"sim-worker-{int(time.time())}"
        r = client.post(
            "/worker/v1/register",
            json={"node_key": node_key, "kind": "scraper_amazon", "enroll_token": enroll},
        )
        if r.status_code != 201:  # noqa: PLR2004
            print(f"注册失败 HTTP {r.status_code}: {r.text[:300]}")
            return 1
        headers = {"X-Node-Key": node_key, "X-Node-Token": r.json()["node_token"]}

        pulled = client.get("/worker/v1/tasks/pull", params={"count": 20}, headers=headers)
        tasks = pulled.json().get("tasks", [])
        if not tasks:
            print("没有待采任务（先在界面「采集作业」建一个 product_detail 作业）")
            return 0
        ok = 0
        for t in tasks:
            ref = t["target_ref"]
            payload = {
                "title": f"Demo Ceramic Mug {ref}",
                "brand": "N/A",
                "price_snapshot": {"list": 15.99},
                "attrs": {
                    "wpt": wpt,
                    "bullets": ["12oz", "dishwasher safe"],
                    "description": f"Plain demo product for acceptance ({ref})",
                },
                "images": [],
            }
            res = client.post(
                "/worker/v1/tasks/result",
                headers=headers,
                json={
                    "task_id": t["task_id"],
                    "attempt": t["attempt"],
                    "success": True,
                    "payload": payload,
                },
            ).json()
            status = "✓" if res.get("accepted") else f"✗ {res}"
            sku = (res.get("product") or {}).get("master_sku", "-")
            print(f"{status} {ref} → product {sku}")
            ok += int(bool(res.get("accepted")))
        print(f"回传完成：{ok}/{len(tasks)}（产品见「产品库」页）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
