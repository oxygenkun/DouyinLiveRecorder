---
name: runtime-pin-verification
description: 修改运行时下载源、钉定、签名或发布获取链路
---

# 运行时钉定验证

约束事实源：`AGENTS.md`；构建、依赖与运行时基线的长期约束见 `docs/agent-reference/known-pitfalls.md`「构建产物、依赖与运行时基线」。先判断修改的是发布、运行期安装还是 JS 执行，三类校验不能互相替代。

## 找到修改面

| 类别 | 入口与核验位置 |
| --- | --- |
| 发布 ffmpeg / node | build_exe._PINNED_RUNTIME_SHA256、_is_pinned、_slot_is_gated、require_pinned_hashes |
| 运行期自动安装 | ffmpeg_install / ffmpeg_linux_download / node_install，官方哈希与显式 TOFU 降级 |
| JS / wasm | utils._JS_SHA256_EXPECTED、资源目录与下载点 |

SOURCE_BUILD_PROVENANCE 只用于 CI 自源码构建且上游无公布值的产物，不是绕开远程归档验证的通用许可。

## 更新来源或版本

1. 从上游公布的哈希或签名取证，记录版本与实际归档类型；node 按下载扩展名挑 SHASUMS256.txt 对应行，zip / tar.gz / tar.xz 的值不通用。
2. 发布 ffmpeg 同步 _FFMPEG_DOWNLOAD_URLS、钉定值/签名模式、tests/test_build_exe.py 的 _ALLOWED_HOSTS；退役主机移出。Linux 同步运行期直下 URL；新增 runner 同步映射和矩阵。
3. macOS 使用版本化 ffmpeg / ffprobe 归档，联动 _EXTRA_EXECUTABLE_URLS 和 _RUNTIME_GPG_SIGNATURES；签名地址等于归档地址 + .sig，两支版本一致。不恢复 getrelease 滚动 API。
4. Linux 用受支持的月末 releases/tags 标签，不用 latest 或短保留日更标签。归档按名称找件，不假定目录层级。
5. 签名模式同时核对 OFFICIAL_SIGNATURE_PIN、已登记签名配置及完整主钥指纹，验证真实构建路径确实调用验签；标记本身不能放行，下载失败不能跳过验证。

在线来源层保持默认 off；verify 仅核对，adopt 仅允许自身已验签、指纹已钉定的清单。同源在线 digest 不能代替独立信任基线。运行期优先核验官方哈希，取不到才按现有显式降级处理；TOFU 镜像默认关闭并告警。

## 验证选择

按受影响路径选择 test_build_exe、test_ffmpeg_install、test_ffmpeg_linux_download 和 test_node_install 用例，仅在需要 commit 时运行 pytest 与覆盖率检查。

用例覆盖未钉定、签名失败与缺件的失败路径，不能只测下载成功；不自动发起真实发布。
