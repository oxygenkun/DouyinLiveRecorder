# 可选浏览器 mockup

仅在用户需要交互式布局或方案比较、现有预览方式不足时使用。普通图表、解释或澄清无需专门启动服务，不要求固定邀请话术或每屏结束一轮对话。

脚本位于本技能的 `scripts/`，命令从该目录执行或使用绝对路径：

```bash
./start-server.sh --project-dir /path/to/project
```

读取返回的 screen_dir、state_dir 和 URL；HTML 写入 screen_dir，浏览器点击记录在 state_dir/events。默认使用 frame-template.html 包装片段，helper.js 提供选择交互；需要完整页面时可自带 HTML。

- 会回收后台进程的环境使用 `--foreground`；服务须按当前工具支持的进程方式运行；启动受限就用已有预览或静态文件，不照搬其他 agent 的后台参数。
- 默认只监听 loopback；远程展示先确认访问方式，不自行改绑 0.0.0.0。
- 项目会话产物在 `.brainstorm/`；把它视为任务产物，按用户需要保留，不写入技能安装目录。
- 结束时用 `./stop-server.sh <session-dir>` 关停自己启动的服务；清理遵守根文件范围，不删其他会话。

仅在实际使用此工具时检查相关脚本与模板，不把整个前端工具读入普通开发任务。
