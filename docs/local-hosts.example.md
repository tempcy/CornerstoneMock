# 现场地址对照（模板）

复制为 `docs/local-hosts.md` 后填入真实值。`local-hosts.md` 已加入 `.gitignore`，勿提交。

## 网络与主机

| 占位符 | 角色 | 真实值 |
|--------|------|--------|
| `<PF_SENSE>` | pfSense 主干侧 / DNAT 入口 | |
| `<L3_NET>` | 公司主干网段 | |
| `<AGENT_HOST>` | DMZ 上的 CornerstoneAgent 主机 | |
| `<DMZ_GW>` | pfSense DMZ 网关 | |
| `<DMZ_NET>` | Agent 所在网段 | |
| `<L1_GW>` | 工控网接口 | |
| `<L1_NET>` | 工控网段 | |
| `<L1_HOST>` | 工控网主机（泛指） | |
| `<L2_IF>` | pfSense L2 接口 | |
| `<L2_NET>` | L2 网段 | |
| `<HTTP_PROXY>` | 公司 HTTP 代理 | |
| `<GC6_IP>` … `<GO9_IP>` | 各仪器 Bridge | |
| `<ZHIBAO_HOST>` | 智宝门户 | |
| `<QWENPAW_HOST>` | QwenPaw 实例机 | |
