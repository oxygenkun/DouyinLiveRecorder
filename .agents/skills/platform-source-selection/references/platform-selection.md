# 平台候选检查

约束见 `AGENTS.md`「流地址探针与可达性校验」「平台接口、签名与流地址解析」。

- 虎牙：检查 _PROBE_BACKOFF_PLATFORMS、_FLV_FIRST_PLATFORMS 与主循环周期，避免退避刚记入就过期。
- 斗鱼：保留同 token HLS 候选和 HLS 优先；解析使用 extract_douyu_rid_param / extract_douyu_room_id，域名扩展同时覆盖 URL 准入和平台分发。
- HLS 配置：分别测试关闭、平台排除、仍有 FLV 兜底与最终无源；只在配置剔除导致无源时给恢复指引。
- HEVC：保存格式矩阵覆盖 TS/MKV/MP4 与不支持类型，确认 h264 原画备选仍在，不只断言最终 URL 非空。

新增候选应通过同一安全与可达性流程；探针末位放行不代表允许内网 URL。
