# Terminal / VS Code 连接日志

两个页面共用 `static/connection-log.js` 和 `static/connection-log.css`，在页面主脚本之前加载。`[data-connection-log]` 是入口挂载点：Terminal 欢迎页和终端工具栏各一处，VS Code 顶栏一处。VS Code 专注模式保留入口和实时状态；Terminal 全屏时把对话框移入全屏元素。支持 Escape / 关闭按钮，关闭后焦点回到打开日志的按钮。

日志包含时间、连接状态和步骤，自动滚动到最新事件，只在内存 DOM 保存最近 200 条，刷新清空。浅色和森林绿共用已有颜色变量。桌面入口直接显示更新时间，窄屏在日志面板查看最后更新时间。

## 接口

- `window.MonitorConnectionLog.update(state, message)` 更新实时状态、最后更新时间，追加与上次不同的安全事件。重复心跳不增加行数。
- `window.MonitorConnectionLog.note(message, state)` 追加诊断步骤，不修改实时连接状态。state 可省略，默认当前状态。
- 支持 `loading` / `ready` / `reconnecting` / `offline` / `error` / `idle`，分别显示连接中、已连接、重连中、离线、错误、待连接。旧页面的 `loading` 恢复文案会映射为重连中，明确离线文案会映射为离线。

## 隐私边界

不拦截终端输入/输出、聊天、请求载荷、票据、URL、Cookie 或错误原文。所有消息都先经过固定文案白名单/分类，未知错误只展示通用提示。不会把传入消息持久保存。可读原始错误仍由页面现有状态区域负责，日志不复制这些原文。

允许两种完全匹配的数值诊断：

- `API open · HTTP 503 · 152ms`：action 只能是 targets/open/status/recover/details，HTTP 为 100–599，耗时为 0–999999 的十进制整数。
- `WebSocket 已关闭（代码 1006）。`：只保留四位数字代码，不记录 close reason。

建议固定步骤文案：正在恢复编辑器认证。／正在重新建立编辑器会话。／编辑器页面未响应，正在恢复连接。／正在探测后台连接状态。／正在确认编辑器页面响应。／正在等待自动重试。

Terminal 记录 targets/open 请求结果与耗时、status 请求失败、WebSocket 开始/握手/关闭代码、兼容连接、重连失败/恢复、用户重试/断开、网络离线/恢复。online 仍使用已有探测流程，真实 pong 或 HTTP 读取确认后再恢复已连接标记，不另开 Shell，不重发输入。

## 本地验证

全部使用本地 fixture 和模拟 API/WebSocket，不操作真实云端任务。

- `.venv/bin/python scripts/connection_log_smoke.py`：先确认缺少组件时失败；实现后验证 Terminal 连接/重连/断开/离线、online 保留健康传输、敏感内容排除、严格诊断格式、IDE 专注模式入口、200 条上限、自动滚动、刷新清空、焦点恢复、全屏、浅色/森林绿、320–1440px 和窄屏横向布局。
- `.venv/bin/python scripts/cci_terminal_smoke.py`：两站点 CCI/ACP、输入与 resize、ANSI/中文、目标选择、HTTP fallback、延迟 WebSocket。
- `.venv/bin/python scripts/mobile_terminal_smoke.py`：移动布局、IME、快捷键、剪贴板、原 Shell 恢复、不重放输入。
- `.venv/bin/python scripts/session_theme_smoke.py`：两页面浅/深色、跨标签主题、五种宽度。
- `/Users/charles/.node/bin/node --test scripts/terminal_efficiency_check.js`：6 个输出批处理/顺序/ACK/取消测试。

日志截图由专用 smoke 保存到 `data/connection-logs/`，仅为 fixture。

补充：健康探测状态为 `checking`（检查中）；已在VS Code的真实请求耗时、自动修复和工作台存活检查中接入，并修正Terminal首次连接文案分类。两个页面实机均验证已连接。
