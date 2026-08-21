---
summary: "HEARTBEAT — 可选巡检"
read_when:
  - heartbeat 轮询
---

# HEARTBEAT.md

# 保持注释-only 可跳过 heartbeat API 调用。
# 若启用仪器巡检，取消下面注释并保持精简：

# - GET http://<PF_SENSE>:8090/health
# - POST /v1/tools/list_instruments （lab_id=lab-2lg, online_only=true）
# - 若 lab-2lg 关键仪器（默认 GC8）离线：简短告警，勿重复长篇
# - 不要在 heartbeat 里跑 collect_instrument（过重）
