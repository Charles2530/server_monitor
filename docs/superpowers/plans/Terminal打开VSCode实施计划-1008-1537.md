# Terminal 打开站内 VS Code

目标：CCI Terminal 工具栏提供新标签页链接，打开同一站点、任务、实例和容器；保留原 Shell。用户已确认网页版、仅 CCI，并允许 tmux 目录无法可靠识别时省略。

## 设计与实施

- 修改 static/terminal.html、static/terminal.css、static/terminal.js，添加原生链接并沿用工具栏样式。仅在本机桌面浏览器、有效 CCI 目标已加载时显示，链接带 site、task_id、instance、container 和 task_view。选项变化时更新，刷新目标时清除旧链接。
- 修改 static/ide.js，读取明确的 instance/container 参数并验证格式；明确目标优先于历史缓存，从云端目标列表验证后打开。目标失效或参数缺失时显示错误，禁止自动改连其他容器。普通 IDE 入口保持原有行为。
- 在本机忽略的 scripts/terminal_vscode_smoke.py 中通过真实浏览器加载源码，模拟云 API；验证双站点、多目标、历史选择冲突、无效目标、ACP/手机入口隐藏、新标签页与 Shell 保持、失败恢复。先确认旧代码因缺少入口失败，再实现并复验。
- 语法检查、源码 diff 检查后，将 4 个静态文件带备份同步至本机现有部署。静态更新无需重启服务，验证 HTTP 文件与源码一致。

## tmux 目录评估

本机 tmux 手册确认 pane_current_path 可提供指定 pane 的路径，client_tty 可识别客户端 TTY；没有当前会话上下文时，目标解析可能选择最近使用的会话。server/terminal_bridge.py 的 Session 只记录云 WebSocket 和 I/O 状态，没有远程 TTY/PID/pane 身份；独立编辑器准备 Shell 无法确定网页当前看到的 pane。当前也没有 Shell 目录上报。可靠支持需要额外建立连接与 TTY 的映射或 Shell/tmux 集成，不能仅用全局 tmux 查询。此次不自动推断目录、不向用户 Shell 注入探测命令，保留编辑器现有目录行为。

## 验收边界

浏览器自动化使用模拟云接口，验证跳转身份与终端生命周期，不代表真实云端首次编辑器安装通过。验证产物保留本机，不上传测试源码。
