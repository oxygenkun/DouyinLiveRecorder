---
name: platform-source-selection
description: 修改平台解析、选源、探针、画质映射或新增平台
---

# 平台选源与画质

约束事实源：`AGENTS.md`「流地址探针与可达性校验」「平台接口、签名与流地址解析」「画质档位与选源映射」「装饰器与平台兜底契约」。按修改路径选择参考：

| 修改范围 | 参考 |
| --- | --- |
| stream_select 探测、复核、退避、客户端 | [探针](references/probe-reachability.md) |
| 平台候选顺序、HLS 配置 | [平台选源](references/platform-selection.md) |
| stream 的请求档位与实际画质 | [画质映射](references/quality-mapping.md) |
| spider 平台函数与 JSON 兜底 | [解析契约](references/parser-contract.md) |

新增平台先核对 URL 准入与 main 分发表、返回结构、Cookie/代理/UA、画质和脱敏；只测试函数返回值不足以证明房间可进入监控。

相关测试从 test_stream_select、test_stream、test_spider、test_spider_hardening、test_platform_dispatch 中选；新安全不变量做变异验证，真机与完整检查时机见根文件。
