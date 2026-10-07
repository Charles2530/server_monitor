# 全站检查与 GitHub 更新（2026-10-07）

基于远端 `bdbb2a2`，保留 Mac mini、MacBook Air 和 Pro 各自的运行配置，合入本机尚未提交的队列响应和渲染优化。

## 本轮修复

- Python 3.9 在服务事件循环启动前创建 Collector 时，原 semaphore 绑定到了初始化循环。高性能配置的 8 个并行队列任务争用 6 个 HTTP 槽位，会使其中 2 个任务读取失败。新增测试先复现 `partial`，随后将请求和指标 semaphore 的初始化移入异步入口，8 个任务均成功。
- 队列并发验收参数化为 3、4、6、8，兼容各机器配置，不再依赖测试机恰好使用 6 个槽位。
- 修正三个过时的浏览器验收：GPU 数值单独定位，避免把整组空闲说明当作数值；分区用例补齐 TokenHub 模拟响应；队列用例切换到可见页面再检查，适配隐藏页暂停渲染。没有放宽功能断言。

## 最终验证

- 完整 Python：827 passed，5 条现有 websockets 弃用警告。
- JavaScript：64 passed（前端、日志和 Terminal 行为及效率）。
- 33 组浏览器/集成脚本完成；初次发现的上述 3 个旧测试已修正并分别重跑通过。
- 独立审查发现的两项问题均已修复，复审相关 77 项测试通过，无新增发布阻断。
- `git diff --check` 通过。

浏览器脚本：

- `scripts/cci_ide_smoke.py`
- `scripts/cci_terminal_smoke.py`
- `scripts/command_logs_smoke.py`
- `scripts/connection_log_smoke.py`
- `scripts/design_smoke.py`
- `scripts/history_smoke.py`
- `scripts/ide_auto_repair_smoke.py`
- `scripts/ide_continue_smoke.py`
- `scripts/ide_entry_smoke.py`
- `scripts/ide_experience_smoke.py`
- `scripts/ide_network_smoke.py`
- `scripts/inventory_smoke.py`
- `scripts/mobile_gateway_smoke.py`
- `scripts/mobile_recovery_smoke.py`
- `scripts/mobile_task_workspace_smoke.py`
- `scripts/mobile_terminal_smoke.py`
- `scripts/mobile_ui_smoke.py`
- `scripts/navigation_smoke.py`
- `scripts/partitions_smoke.py`
- `scripts/release_restart_smoke.py`
- `scripts/restart_smoke.py`
- `scripts/session_theme_smoke.py`
- `scripts/sites_smoke.py`
- `scripts/storage_smoke.py`
- `scripts/task_cards_efficiency_smoke.py`
- `scripts/task_config_smoke.py`
- `scripts/task_rename_smoke.py`
- `scripts/terminal_pty_smoke.py`
- `scripts/theme_smoke.py`
- `scripts/tokenhub_hourly_smoke.py`
- `scripts/tokenhub_smoke.py`
- `scripts/v2_browser_smoke.py`
- `scripts/waiting_links_smoke.py`

## 真实运行环境检查

- maoshanwang、sensecore 的状态、TokenHub、存储、队列和任务历史接口均 HTTP 200；状态和 TokenHub 均已连接。
- 存储分别返回 6 / 38 项，历史分别返回 180 / 216 项。历史和队列按既有设计后台刷新。
- 队列分别返回 11 / 16 项，其中 10 / 15 项指标正常、各 1 项未知、0 项读取错误。未知上游指标按现有界面显示，不能声称所有云端指标完整。
- 真实页面 Terminal 显示实时连接，VS Code 显示连接可用、Codex 面板已加载。

创建、停止、重命名和异常恢复用本地模拟接口验证，未对真实云任务执行这些变更。生产服务未重启，真实运行检查针对当前部署；本轮 Collector 修复随下一次部署生效。没有测试另一台物理电脑、真实断网或所有可能的云端故障。

本机完整测试日志位于 `/tmp/server-monitor-final-pytest.log`、`/tmp/server-monitor-final-node.log`、`/tmp/server-monitor-full-smoke/`；日志及截图不提交 Git。
