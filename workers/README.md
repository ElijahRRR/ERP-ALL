# workers/ — 本地采集 worker（R1-09 起）

采集 worker 在 Owner 本地机器运行、出站拨入云端 API 领任务（D-Q47），独立打包、独立发版。
移植源：amazon-scraper-v3（D-Q42）。协议（注册/心跳/领任务/回传）见 specs/001-domain-model/09-platform.md §worker_node。
