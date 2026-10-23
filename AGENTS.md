# AGENTS.md

## code style and dev env
- use modern python type hint (>=python3.14, prefering built-in type or keyword instead of types in typing module)
- use `uv` to manage project and python dev tools

## 项目结构

[目录参考](docs/agent-reference/project-structure.md)。CLI / GUI / Web 入口分别为 `main:main`、`gui:main`、`web:main`。

## 项目级 Skill

下表与 `.agents/skills/` 的一级技能目录一一对应；触发场景与各 skill 的 description 同步。新增、删除或改名时同批更新，运行 `python scripts/check_skill_agents_consistency.py`。

| Skill | 路径 | 触发词 / 场景 | 覆盖领域 |
| --- | --- | --- | --- |
| DLR 开发手册 | `.agents/skills/dlr-dev-playbook/` | 新功能设计、测试编写、运行日志排障、跨平台故障或语法审查 | 按场景加载参考步骤 |
| ffmpeg 命令构造 | `.agents/skills/ffmpeg-command-construction/` | 修改 ffmpeg 录制命令、保存类型、输入输出参数或分段容器映射 | 构造入口与专项验证 |
| 平台选源流程 | `.agents/skills/platform-source-selection/` | 修改平台解析、选源、探针、画质映射或新增平台 | 选源与解析配方 |
| 运行时钉定验证 | `.agents/skills/runtime-pin-verification/` | 修改运行时下载源、钉定、签名或发布获取链路 | 来源核验与联动修改 |

`.agents/skills/` 必须入库：gitignore 用 `.agents/*` + `!.agents/skills/` 反选，不能忽略整个 `.agents/` 后再反选；Docker 仍排除整个 `.agents/`。技能 shell 脚本由该目录 `.gitattributes` 钉 LF。目录缺失时一致性检查必须失败，不降级 skip。

### 项目级 Hooks / Commands（生命周期资产）

- `scripts/agent_hook_guard.py` 提供确定性反馈；本地注册位于被忽略的 `.qoder/settings.json`，不随仓库分发。PreToolUse 可用 exit 2 阻断；PostToolUse 写入已发生，只反馈并由检查兜底。未知事件 fail-open，不能把 Hook 故障当代码故障。
- Hook 不自动修改文件、不替用户扩大清理范围；约束改变时同步 Hook 与 `tests/test_agent_hook_guard.py`。
- `/dlr-dod` 的本地入口 `.qoder/commands/dlr-dod.md` 只引用「完成定义」，不复制检查清单。

## 依赖管理

- `pyproject.toml` 声明、`uv.lock` 锁定；CI / Docker 用 `--locked`。基础安装 `uv sync --locked`，桌面 GUI / Windows Web 托盘加 `--extra gui`，开发加 `--extra dev`，打包再加 `--extra build`；不以 `--dev` 替代现有 dev extra。
- `requirements.txt` 是基础依赖的 pip 兼容清单，与 project.dependencies 同步。比对时去行内注释、归一包名与规格集合；GUI / dev / build 依赖不放回基础清单。
- 改版本、依赖或包清单后运行 `python scripts/sync_metadata.py` 生成 uv.lock 与已跟踪 egg-info，禁止手改派生版本字段；只读核验用 `--check`。
- 安全下限以依赖声明和审计为准；新增或抬升依赖下限跑 `deps-audit`，不随意加漏洞忽略项。pip-audit 属审计工具，不进运行时依赖。
- protobuf 上限放宽前先用兼容的 protoc 重新生成，禁止手改 `douyin_pb2.py`；引用的动态消息字段与现有 `.pyi` 同步。
- 收集期 ModuleNotFoundError 先核对 venv 与锁文件，不改业务代码掩盖缺包；运行 pytest 需要基础依赖及 dev extra，Qt 测试还需 gui extra 与系统库。

## 测试

- 测试配置在 `pyproject.toml`；pytest 要求 warnings summary 为空。只在全局配置中有来源说明地忽略第三方告警，不过滤项目自身告警；协程 RuntimeWarning 修根因。
- 日常只跑改动相关的测试与检查；完整检查的时机见「格式化命令」。结果必须来自最后一次相关编辑之后，缺环境、skip、未执行均如实报告。
- 新测试优先放在对应模块的 `tests/test_<module>.py`；跨模块行为用清楚的专题名。测试必须驱动生产实现，不在测试中重写锁、缓存或限流逻辑。
- 安全测试验证行为或稳定不变量，不用钉旧变量名/旧文案的否定正则冒充安全保障。
- 安全不变量、黄金命令、节流/锁/退避的新用例须做变异验证；平凡断言或已完整覆盖的调整可免。变异必须带 `MUTATION-<短id>` 标记，在同一进程 `finally` 中按原字节还原并核对；交付不得残留标记。
- 前端测试使用 Node 内置 `node:test`，无需 npm。新增 `tests/frontend/*.mjs` 同批添加 Python 包装，并登记 `.github/workflows/ci.yml` 的「Gate frontend tests not skipped」node-id 清单；不另建清单。
- `tests/test_test_hygiene.py` 维护双模收集、变异残留、零收集例外、线程退出等规则；不通过放宽例外掩盖失败。覆盖率阈值以 `scripts/check_coverage.py` 为准，无数据或模块缺失必须失败。

### 测试编写强制约定

- 环境变量用 `monkeypatch.setenv/delenv`，不用 `patch.dict(os.environ)` 整体回写环境。
- 替换 stdlib 属性时浅拷贝为 `types.SimpleNamespace(**vars(module))`，只换被测模块的引用，禁止改全进程共享的 os / subprocess / time。替身还须按本用例对象或路径收窄，其他调用透传。
- patch main 的 Popen 使用类；需支持注解求值时定义 `__class_getitem__`。不要把 Popen 换成 lambda。
- 原子写回的只读测试，对目标路径的 replace 注入 PermissionError，其他路径透传；不能只靠 chmod。
- 测试输出用 tmp_path，跨进程用受控临时目录；不用会话共享目录。live collector 的手跑输出 `tests/_out_live` 会被会话收尾清理，需留存先复制。
- 双模 collector 模块级无网络、sleep、清理或 exit；执行体放 main 守卫。数值参数先取原文再 `.isdigit()` 校验，不能只排除 `-` 前缀；清理只认本平台前缀并先判类型。收集须秒级返回，不强求手动脚本都有离线用例。
- 并发测试 `join(timeout=...)` 后在同一函数断言线程 `not is_alive()`；平台分流替身覆盖 Windows 与 POSIX，不靠 skip 跳过可模拟分支。
- 本地化系统工具的子进程输出按字节比较；涉及码页的用例须能单文件运行。已知 UTF-8 文本协议按自身契约处理，不一概禁用文本模式。
- `pytest.importorskip` 探测真正使用的子模块并传 `exc_type=ImportError`；CI 补齐系统库并对必要 Qt 模块设收集闸门，不能靠整模块 skip 伪装通过。
- 保留 conftest 的凭据缓存、弹幕 hub、恒等翻译隔离；不用静默跳过 fixture 掩盖缺依赖或污染。Qt 测试须还原全局翻译登记表。

### 测试收尾清理临时脚本

优先用内联检查或 tmp_path；一次性脚本可放系统临时目录，创建时记路径、失败时也清理。只删除本次创建的明确文件，不按 tmp/mock 前缀全仓删除，不顺手清缓存、git clean 或删除运行期数据。safe-delete 处置见风险控制，收尾用 `git status --short` 核对并列明无法清掉的产物。

## 构建命令

```bash
uv sync --locked --extra build --extra gui
uv run --locked --extra build --extra gui python build_exe.py --smoke
uv run --locked --extra build --extra gui python build_exe.py --dual
```

其他参数先看 `build_exe.py --help`。镜像通过 APP_VERSION 注入 pyproject 版本，`docker compose up -d` 本地构建；不提交运行时配置。

### 打包与冒烟测试语义（build_exe.py）

- CLI / GUI / Web 三入口共享 `_internal/`；config、ffmpeg、node 与 exe 同级。JS 签名资源按模块路径打入 `_internal/`。
- 冒烟结果按 `FATAL_MARKERS` 与进程状态判定；headless 特定良性报错只允许精确豁免，不宽泛吞异常。
- zip 名整体拼接，不能用 with_suffix 截断带点版本；拒绝路径分隔符，双产物须名称互异且均存在。`--dual` / `--no-zip` 互斥；陈旧发行 zip 的清理由构建入口自身负责。

### 产物体积检查

`python scripts/report_bundle_size.py dist/DouyinLiveRecorder` 实测后才报告体积。新增 BLOAT_EXCLUDES / QT_BLOAT_FILES 必须证明运行期不可达，并实跑冻结入口；不用 collect_all('PySide6') 全收。CLI/Web Analysis 排除 Qt，GUI 不排；固定成本依当前产物判定，不把 legacy Tk 自动算作现行 GUI 必需。

### CI / workflow 约定

- 网络安装重试复用 `.github/actions/retry`，smoke_test 的配置畸形统一在 load_config 返回 rc=2，调用使用 fail_fast_codes；不把失败改成 warning/0 让 CI 变绿。
- ci.yml 与 build-release.yml 的 python_build / node_version 同步；动作版本按实际配置维护，第三方非认证动作使用核验过的不可变 SHA，不凭记忆填 ref。
- 发布矩阵、runner 标签及顺序与 `check_runtime_pins.RUNNER_TO_RUNTIME_KEY` / 对应测试同步；变更矩阵时同步产物数量校验。Linux 家族用 startsWith；YAML 行首 `!` 表达式需引号。
- 多架构镜像用原生双 runner 构建，再按提升到 job outputs 的 digest 合成；不用 QEMU 承担全量跨架构构建。只由版本 tag 发布，凭据仅 GITHUB_TOKEN，不增加重写 latest 的调度入口。
- 发布失败由 release-guard 回收空 Release，不删 tag、不关闭产物缺失检查。
- 本地工具、运行期数据排除项同步 gitignore / dockerignore / Ruff / coverage；`.agents/skills/` 入库例外见技能节。覆盖率主任务与 concurrency 配置均用 exclude_also 并保持同源。
- Docker 只支持 CLI / Web：不加 gui extra、Qt/X11 系统库、GUI 目标或 compose profile；dockerignore 排除 gui.py 与 src/gui_qt/，uv.lock 必须进上下文。uv 固定版本及摘要，锁定安装，运行阶段不带构建工具，FFmpeg 独立阶段构建。
- deps-audit 阻断 Python 依赖漏洞；Trivy 镜像审计维持 advisory，升级阻断需维护者明确决定。
- 跑 pytest 的 job 安装基础依赖与 dev extra；Qt 测试补 gui extra 和系统库。`sync_metadata.py --check` 必须零子进程、零联网；维护脚本缺可执行文件时明确降级，不阻断后续独立步骤。

## 格式化命令（检查唯一基准）

本节 bash 块是 run_gates 解析的检查清单；不要在其他文档或技能复制。

```bash
PYTHONUTF8=1 python -m ruff format --check --diff .
PYTHONUTF8=1 python -m ruff check --no-fix .
python scripts/compile_po.py --check
python scripts/check_version.py
python scripts/check_runtime_pins.py
python scripts/check_skill_agents_consistency.py
```

- **完整检查仅按用户明确指令执行**：不自动、重复或并行启动 run_gates / 全量 pytest。日常运行直接相关检查；未跑全量如实说明，不阻止已授权任务交付。
- 完整检查入口 `python scripts/run_gates.py`；排障用 `--list` / `--only` / `--keep-going`。检查保持只读，不带 fix；本地、CI、pre-commit 消费同一锁文件的工具版本。
- pytest 与覆盖率不复制进上方清单；用户要求完整验收时，按「完成定义」一并执行。中文 Windows 检查保持 PYTHONUTF8=1。

## 完成定义（Definition of Done）

1. 完成授权范围，相关检查通过并报告实际命令、结果、未执行项；完整验收仅按上节时机执行，包含 run_gates、pytest（0 warnings）与覆盖率检查。不得把编辑前的结果当作编辑后的证据。
2. 录制/选源/ffmpeg/平台解析改动在有条件时对受影响平台做增量真机验证，报告脚本、脱敏 URL、PASS/WARN/FAIL 和适用读数；无外网或无活房间则明确未验证范围及用户复验方法，不声称真机已通过。
3. 保留并运行受影响的回归用例；缺少必要覆盖时补测试。
4. 用户可见行为、配置/依赖、架构或长期约定变化更新相应文档；重大变化追加中英 CODE_WIKI 日志。小修、测试、注释不强制多处登记，长期规则只在本文维护。
5. 按清理节核对本次临时产物和 git diff，不动用户已有变更。
6. 最终回复说明改动、验证和实质限制；不强制写工具私有记忆、每日流水账或固定回滚模板。

## 流程编排技能的使用边界

新功能或产品行为不清楚时，先给足以决策的简短方案，必要时一次确认；已明确的任务、修复、重构、配置与文档改动直接推进，不重复审批。用户明确要求治理式流程时再加载对应技能并确认 run 范围与 artifact root，不把技能安装目录当产物目录。设计不强制提交，不依赖未安装技能。

## 并发与线程模型

### 基础模型

每房间独立线程，以各自 asyncio.run 驱动异步请求；不存在共享全局事件循环。

### 调度中枢 src/scheduler.py

- 并发由 ConcurrencyScheduler 管理：最大同时录制数为 0 时动态调速，非 0 时固定并发；动态容量不低于配置下限。ResizableSemaphore 允许 0 暂停，缩容不回收在用槽位。
- host 熔断 open→half-open 只放一个带租约探针，超时可重授，避免未上报轮次永久占住探针。计数增量维护，不在持锁热路径反复 sum(deque)。
- start_record 先初始化 record_host，再在平台分派前 allow 预检；网络与录制信号量保持分工，录制槽必须在 Popen 前 acquire，启动及运行全部包在 try/finally 中释放。

### 录制结果反馈约定（main.py / src/stream_select.py）

ffmpeg 按退出码上报成功/失败，禁止轮末无条件成功。解析成功和直下成功也须反馈 host 样本；快速失败按实际 `-i` 地址标记退避，成功只清实际成功地址，白名单由 stream_select 维护，不扩大到斗鱼。

### 锁的强制约定

- 异步锁按当前事件循环缓存/重建，不建跨循环共享单例。线程锁的种类与持有范围以当前源码为准，不靠历史清单推断。
- 不跨 await 持有线程锁；异步请求去重复用 cookie_cache 的 singleflight，缓存状态在零 await 的短临界区读写。RLock 仅解决同线程重入，不能提供协程间互斥。
- 锁所有权在函数内部或调用方明确选一处；变更时搜索全部调用点，避免 Lock 嵌套死锁。

## 关键约定

- 版本唯一声明在 pyproject；入口经 importlib.metadata 动态读取，Docker 经 APP_VERSION 注入；派生元数据用 sync_metadata 更新，文档版本按需同步。
- 布尔解析复用 config_bool.parse_config_bool，读取写回复用 config_io.read_config_bool；前端 parseConfigBool 保持同一 token 集合，未知值走默认且不覆写用户原文。
- 改公共符号前用 rg 搜索调用点、测试、资源和 standalone 孪生实现；删符号后不留悬空调用。main.py 内部的 main 是函数，不作模块前缀使用。
- JS 文件增删与 utils._JS_SHA256_EXPECTED 同步；migu.js 返回完整签名 URL，调用方不再拼过时参数。
- 启动缓存清理只限程序顶层与 src 直接含 .py 的目录下 pyc/pyo；不递归全仓。基线用版本 + 路径/源码内容哈希，不能用 size/mtime；冻结或开关关闭时零 I/O，基线失败仍清理并告警。
- 停止时日志归档复用 log_archive.archive_runtime_logs；Windows 改名前关闭句柄，GUI 子进程及测试按现有开关早退，不重开测试对真实日志的副作用。

## 已知坑（避免回归）

以下只留当前不变量；修改对应行为前查看生产实现和已有回归用例。

### 录制链与房间线程主循环

录制主链不受 if headers 控制；real_url 为空告警并等待重试，不启动 ffmpeg。注释 URL 的检查必须早于平台解析，房间退出在 finally 注销弹幕。录后脚本命令/异常日志脱敏，超时回收子孙进程并对后续 communicate 限时。

### 流地址探针与可达性校验

- HEAD 非 2xx 走限量 Range GET（200/206 可达）；401/403 按现有复核流程重试，末位候选允许告警交 ffmpeg 决定，但不得绕过协议/内网安全判定。
- 虎牙专用退避、FLV-first、抖动和至少一个主循环的退避窗口必须保留；斗鱼 HLS 优先。HLS 整组排除与候选调序是不同操作。
- UA、proxy、verify 在签名、探针及 ffmpeg 间保持约定一致；UA 修改核对 MOBILE_UA、ffmpeg 默认、room.HEADERS 与 B站 H5。
- 重定向安全校验复用 async_http._build_client 与 stream_select._probe_client 的接线点；不另建客户端绕开，不缓存 DNS 判定。响应钩子不等于建连前防护，不能声称已闭合 DNS 重绑定。
- 响应决定的落地 URL 检查域族、userinfo 和内网地址；失败保留用户原始 URL、剥离 Cookie，下游拼接主机再次校验。sync_http opener 显式协议白名单，不修改进程全局 opener。
- 小红书链接统一 parse_xhs_live_url；房间号只收 ASCII 数字，未开播保留主播信息与监控作业，失败不得伪装未开播或请求 /user/profile/None。

### 弹幕采集与 SRT 写入

- WS 显式 proxy=None；B站 buvid 走真实注册链，AUTH_REPLY 非零须失效缓存并断开，无响应由 watchdog 限时。
- collector.stop 先置停止事件再读 loop；_run 先发布 loop 再查事件，关闭协程限时。stop 在录制轮询循环外并保持幂等。
- SRT 外部用户名/正文转义换行及 -->，保持单块/单时间轴；分段 end 钳在 start 与分段长度间。视频 %03d、音频 %02d 占位剥离须兼容，SRT 三位编号对齐。
- 抖音空 Cookie 动态获取 ttwid；signature 保持上游未编码契约，不顺手 quote。B站 host 轮换仅在候选耗尽或停止后经 _report_close 恰一次上报最终关闭。

### 平台接口、签名与流地址解析

- 斗鱼保留同 token FLV→HLS 候选；数字 rid 用现有提取 helper，新域名同时登记 PLATFORM_HOST 与分发表。
- 抖音数字房号和抖音号均可传 web_rid，不加多余重定向；200 空响应先排查 UA/Cookie，用户接口用 DESKTOP_UA。
- web/enter 空列表只有显式离线 room_status 才静默返回，否则保留 HTML 兜底；roomStore 先解析完整 JSON 片段，失败才补括号。
- hevc_flv_url 保留 codec=h265 标记；utils.get_query_params 返回 list，不与 spider.get_params 的 str 混用。

### 画质档位与选源映射

- 蓝光子档折叠 BD，不塞入 QUALITY_MAPPING；中文档位同步 get_quality_code、web_config.QUALITY_KEYWORDS、main 白名单、web/index.html。
- FLV/HLS 各自按原始请求索引钳制，不复用另一侧已钳下标。虎牙不可用档降级或原画，斗鱼最多回退两档，保持 is_live=True。
- HEVC 替换保留 h264 FLV 备选；保存格式允许集与选源、实际输出容器一致。HLS 被配置剔除且最终无源时提示恢复开关，健康轮不刷告警。

### HTTP 客户端复用与连接管理

- 探针客户端仅单次选源复用，不升级全局缓存或禁用 keepalive；业务请求头逐请求传，避免候选间凭据污染。
- sync_http Session 按线程复用，不跨线程共享；不安全 SSL context/opener 惰性建立。没有生产调用时不宣称已获得性能收益。
- 所有出站读取受上限体系约束：异步在唯一 response 钩子，sync_http 和探针走各自限量 helper；超限走原失败出口，不返回半截内容。新增读取点登记 tests/test_outbound_read_caps.py，不能靠只读状态码豁免。

### 并发、事件循环与锁

跨循环淘汰 AsyncClient 不在错误循环创建/调度 aclose；沿用现有释放策略，避免永不完成协程与 RuntimeWarning。Qt QApplication 只在主线程创建；legacy Tk 进程不得混入 Qt 托盘。线程死锁、段错误先取证再归因，不预判为环境噪声。

### ffmpeg 命令构造与容器格式

输入、路径、执行、平台分发复用现有构造入口，不在各平台复制命令。reconnect 选项在 -i 前且紧跟值，HLS 去 reconnect_at_eof；segment_format 经 SEGMENT_FORMAT_BY_SUFFIX 查表。录制命令省略可选 thread_queue_size，重新引入前验证支持版本。操作与测试见 ffmpeg skill。

### 日志、控制台与 GUI / 后台模式

Web 安全拒绝启动必须早于 stdio 重定向，保留日志兜底；重定向后重建 loguru 控制台 sink，GUI 父进程不持录制日志句柄。logger.add 前检查无控制台的空 stderr；异常日志带类型和上下文并脱敏，房间上下文走 extra[room]。退出状态按进程实际结果判断。Web 托盘成功时主线程运行 Qt、服务放后台线程；降级时服务在主线程运行，保留 watchdog、Ctrl+C 退出与 server.should_exit 接线。

### i18n 文案、目录与占位符

- 后端四语目录键集合一致，修改 po 后重编 mo；前端内嵌目录按对应 UI 文案同步。日志用 i18n.tr(常量模板, **kw)，不先 f-string 插值，形参与占位符同名。
- 提取器看不全 push 正文、间接模板和 raise 文案，新增可见串核对目录覆盖测试；错误产生点翻译，配置键、正则和机器码不翻译。删孤儿键需核对 AST 调用与测试夹具。
- tr 的容错包括 AttributeError / TypeError，不能让翻译异常盖掉原故障。GUI 静态标签用 T 登记，动态状态在产生点翻译；语言切换重译、哨兵、动态页、托盘均刷新，配置保存与重译分开容错。
- Qt 重译清除已销毁 C++ 包装条目；构造标签与 T 登记同一 msgid。解析子进程状态行按翻译后文案惰性构建，语言变化后复验缓存键；未翻译的遗留输出按真实契约匹配。

### 配置键名、布尔口径与 SSL 验证

- 配置键不含 = / :，读取 utf-8-sig，键名对比大小写归一；旧迁移键不盲补。补写原子、持锁、幂等，写回结果可被 strict parser 重读。
- 容错读取用 config_read.tolerant_config_parser；GUI 不经 config_io 引入 main 锁体系。utils 中该依赖保持函数内延迟导入，避免循环依赖。
- SSL 平台覆盖经 http_config，异常平台只追加不删除用户项；全局已禁用校验时不重复应用覆盖。

### 凭据与敏感配置脱敏

脱敏边界见风险控制。改出站异常日志时核对 tests/test_exception_log_masking.py 的现存棘轮；新站点必须安全，整改后同步缩减例外，不把当前存量当作新增漏脱敏的许可。

### 类型检查、注释与静态检查

按「Python 版本」「代码风格」执行。Windows 专属 API 只在对应平台运行；不为旧类型检查器改宽业务注解。审查声称语法错误时先用项目解释器编译具体片段，不凭旧语法知识改代码。

### 热路径性能（数据结构 / 正则）

主循环成员检测用 set，保序去重用 dict.fromkeys，确需顺序的队列保持 list；高频正则模块级编译或按 key 有界缓存，不把热路径退回重复线性扫描。

### 构建产物、依赖与运行时基线

- 新增出站二进制/脚本下载点须登记 tests/test_ffmpeg_install.py 的 DOWNLOAD_SOURCES 与 DOWNLOAD_MODULES，官方源优先于镜像，TOFU 源默认关闭且开启/跳过均告警。
- 发布运行时必须 fail-closed 钉定；来源、签名和平台变更按 runtime-pin skill 核验，不能把 CI 实测哈希直接回填成信任基线。发布、运行期安装、JS 三类校验互不代替，新增远程执行代码须归类。
- 在线 pin-source 默认 off；verify 不写期望值，adopt 只采用自身签名已验证且指纹已钉定的清单，同源 digest 不等于来源认证。
- Linux 发布 URL 与运行期直下 URL 同步，版本更换同步钉定；运行期不 import build_exe。安装先找到 ffmpeg/ffprobe 两件再改目标，缺件零副作用；master_allowed 由调用方传入。
- Apple Silicon 优先现有原生 ffmpeg，PATH 判定使用注入前快照并按 realpath 排除自身；standalone 孪生规则同步。
- 主线安全修复核对 standalone 的协议、内网、脱敏、终止、布尔与签名副本，报告同步或不适用原因。
- Docker ARG 在使用前声明；compose 保持 pull_policy: build，不恢复 latest 拉取兜底。不在不可信输入工作流中运行浮动 ref 的第三方动作。

### 装饰器与平台兜底契约

返回 dict 用 trace_error_decorator，返回 str/tuple/None 用 trace_error_decorator_or_none，调用方解包前判空。说明注释放装饰器上方是风格约定；注释不会改变 Python 的装饰器绑定。平台 JSON 兜底走 _loads_dict / _safe_loads 和现有取值 helper，裸 loads 棘轮不得扩大；装饰器变化须核对熔断失败样本语义。

### Web / GUI 面板与接口层

- Web 路由走 _route，模型走 web_models.parse，保留请求校验、线程池派发与 422 语义；不恢复 FastAPI/pydantic。房间表格保持受作用域约束的固定布局与窄屏横向滚动。
- 画质写回共用 update_room_quality，刷新编辑器快照和配置显示源，不把子进程旧设置当现值；显示名查表前去序号前缀。
- 现行 GUI 在 src/gui_qt，令牌集中 theme.py，QSS 用 objectName，等宽字体经 fixed_font。周期 QTimer 异常限频留痕，保留 session_id / stopping / quitting 竞态守卫与停止单飞闸门。
- 输出链按入队→唤醒信号→主线程排空；自然 EOF 与手动停止按会话去重。真实信号通路须被测试驱动，不能仅直调 drain；不加空闲轮询，不在读线程清进程句柄。
- legacy Tk 仅维护其现有回归宿主：after 链 finally 续期、wraplength 防抖迟滞、主题/字体按旧控件契约更新。Tk 几何与字号规则不适用于 Qt，新功能不再套用旧实现。

### 测试质量与审查协作流程

测试隔离、变异和平台覆盖统一见测试节。审查结论需核对当前代码和可复现行为，不能因风格偏好误报语法；环境失败与代码失败分别报告，不能未经取证就忽略崩溃。

## 本机 y123 部署补丁边界

shutdown_manager 管理的录制/转换进程只发一次停止信号并等待，不再进入 terminate/kill 兜底；两个登记表注销同步。关注 feed 只作正向直播提示，缺失或失败不解释为关播。修改时运行 test_local_migration、test_douyin_follow、test_graceful_shutdown 对应用例。
