# 终端目标查询效率优化（2026-10-07）

已部署到本机 Mac mini。CCI / ACP 同一任务、同一登录会话中重叠的终端目标查询共享一次正在执行的读取，减少重复云端访问和并发槽位占用。单次读取仍需等待云端响应。

## 对照测量

使用 `scripts/terminal_target_benchmark.py`，分别加载部署前源码及修改后源码，使用真实解析器和采集器并发限制、模拟 HTTP 传输，每次上游请求固定延迟 20 ms。

| 场景 | 修改前批次耗时中位数 | 修改后 | 上游请求数 |
| --- | --- | --- | --- |
| CCI 单次读取 | 42.97 ms | 42.92 ms | 2 → 2 |
| CCI 8 个重叠读取 | 86.44 ms | 43.86 ms | 16 → 2 |
| ACP 单次读取 | 44.60 ms | 43.63 ms | 2 → 2 |
| ACP 8 个重叠读取 | 87.77 ms | 44.06 ms | 16 → 2 |

结果保存于 `data/terminal-target-benchmark-before.json` 和 `data/terminal-target-benchmark-after.json`。该测试证明重叠读取减少重复请求；耗时是模拟网络环境中的结果，不能推算整站或真实云端提速比例。

## 正确性与验证

- 缓存按解析器/站点、任务、登录请求头摘要区分；CCI 内部地址查询与公开查询分别保存。
- 返回值深拷贝，防止调用者修改共享结果；一个等待者取消不影响其他等待者，最后一个等待者退出则取消并回收上游请求。
- 显式刷新失败后不会重新使用旧成功结果；已完成缓存最多 128 项，过期时间继续使用机器参数。
- 新增 15 项终端查询效率测试；完整 Python 回归 800 passed，5 条现有 websockets 弃用警告。
- 前端、终端及日志 JavaScript 回归 64 passed，记录在 `data/terminal-target-js-tests.log`。
- 部署后 CCI / ACP 各 4 个并发 GET 均返回 HTTP 200，组内内容一致；公开 CCI 结果不含内部 address 字段。未打开终端或执行云端命令。
- 本次线上批次观测：CCI 4767.58 ms，ACP 3397.28 ms。没有同条件部署前线上测量，这些数字仅用于验证可用性，不能作为线上提速证据。记录在 `data/terminal-target-live-smoke.json`。
- 主服务 `/api/state`：HTTP 200、`connected=true`、`status=ok`；手机网关 `/health`：HTTP 200。

## 部署与机器参数

仅原子替换运行目录中的 `server/terminal_target_cache.py`、`server/cci_terminal.py`、`server/acp_terminal.py`，然后重启 `system/charles.server-monitor`。部署后 3 个文件的 SHA256 与测试源码及备份清单一致。

运行目录：`/Users/charles/Library/Application Support/CharlesCloudMonitor`。

Mac mini 参数保持 `m4-balanced`：采集并发 6、指标并发 4、队列并发 6、终端上限 8、目标缓存 8 秒；状态轮询空闲 30 秒、采集中 10 秒、失败 15 秒。实际运行参数和 `config/runtime-profiles/` 中 4 份保存配置均经 SHA256 核验未变。

MacBook Pro 的 balanced / performance 代码预设继续保留。由于没有远程访问资料，未读取或更新 Pro 实机；这两份预设不代表 Pro 实测备份。

## 回退资料

本次备份：`/Users/charles/Library/Application Support/CharlesCloudMonitor-before-target-coalescing-20261007-y_2rbrhn`，包含原解析器文件、原运行参数及 `manifest.json`。工作区指针为 `data/terminal-target-deployment-backup.txt`。

如需回退，只恢复本次备份中的两个解析器文件，移除本轮新引入的 `server/terminal_target_cache.py`，再重启 `system/charles.server-monitor`；操作前核验当前文件仍符合本轮清单，以免覆盖后续改动。保留其他文件和机器参数。
