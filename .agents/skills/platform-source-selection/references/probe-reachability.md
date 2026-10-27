# 探针修改配方

约束见 `AGENTS.md` 与 `docs/agent-reference/known-pitfalls.md`「流地址探针与可达性校验」「HTTP 客户端复用与连接管理」。沿现有 select_source_url → _validate_stream_url / _confirm_get_ok 路径修改，复用受限客户端与读取 helper。

重点测试矩阵：

| 输入 / 故障 | 应核对的结果 |
| --- | --- |
| HEAD 拒绝而 GET 成功，包括 text/html | 不提前返回；限量 GET 仍可确认可达 |
| GET 401/403 | 原样复核；末位回退不越过安全检查 |
| 内网、非允许协议、重定向 | 拒绝危险目标，不把 last_resort 当安全豁免 |
| 多候选、不同 headers / 代理 | 单轮复用客户端，业务头逐请求隔离 |
| 快速失败、下一轮、随后成功 | 退避限平台、持续足够久、仅清实际成功地址 |

节流/抖动测试替换时间或随机源，避免真实等待；不删生产节流让用例变快。UA 改动按根文件核对四处，proxy/verify 按实际请求路径验证。
