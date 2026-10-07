# Mac mini 效率优化与机器参数保留（2026-10-07）

同一份代码继续支持 Mac mini 和 MacBook Pro。本轮仅向本机 Mac mini 部署。

## 实测结果与边界

队列快照已经完成脱敏及 JSON 类型整理，接口改为直接使用 JSONResponse，省去 FastAPI 重复递归转换；异常处理、站点隔离、数据新鲜度判断保持原流程。

离线相同合成数据，每组预热 5 次、测量 30 次，使用真实 ASGI 路由和队列快照：

| 任务数 | 原响应中位数 | 新响应中位数 | 响应字节数（前后相同） |
| --- | --- | --- | --- |
| 11 | 0.905 ms | 0.613 ms | 4,065 |
| 1,000 | 26.894 ms | 9.690 ms | 362,804 |

重现：`.venv/bin/python -m scripts.queue_response_benchmark`。这些数字仅衡量本地快照处理，不包含云端查询，不能解读为整站或云端加载提速比例。

mini 的状态轮询改为：空闲最多 30 秒、采集中 10 秒、失败 15 秒。已知 `next_run_at` 时提前检查下一轮采集；手动刷新和重新显示页面立即读取，隐藏页面停止后续定时请求。完整 3 分钟模拟、每分钟采集 20 秒：原来 18 次状态请求，现在 12 次。Pro 及未配置新字段的部署保持原来的 10 秒频率。来自其他客户端或云控制台的未知时间变更，在空闲期可能最多等 30 秒才发现，这是减少请求的取舍。

## 保存的参数

目录：`config/runtime-profiles/`。

- `mac-mini-before-20261007.json`：本机部署前实际运行的参数，原始字节保留。
- `mac-mini-current.json`：本轮已部署参数，已核对与运行副本完全一致。
- `macbook-pro-balanced-baseline.json`：此前代码中的 balanced 预设，云请求 6、指标 4、队列 6、终端 8、缓存 8 秒。
- `macbook-pro-performance-baseline.json`：此前代码中的 performance 预设，云请求 6、指标 4、队列 8、终端 12、缓存 6 秒。

没有 MacBook Pro 的 SSH 信息，因此后两份是**保存的代码预设，不是从 Pro 读取的实机备份，也不是 Pro 性能测试结果**。没有更改 Pro 的运行服务。核数和内存相同的 Pro 不会获得 mini 专用轮询参数；本轮只识别已确认的 mini 型号 Mac16,10，不推测其他型号。

Mac mini 当前：Mac16,10、arm64、10 核、24 GB；云请求 6、指标 4、队列 6、终端 8、缓存 8 秒。云端并发未上调。

每次部署前会将不同的旧 `runtime-profile.json` 原样存入运行目录下的 `runtime-profile-history/<SHA256>.json`，权限 0600，相同内容不会反复备份。手动指定的配置会标为 manual；在同样硬件上后续部署继续保留它。非法配置在停止服务前拒绝。

恢复保存参数（在对应电脑项目根目录；系统服务按原有迁移流程）：

```sh
sudo /usr/bin/python3 scripts/boot_services.py rollback --user charles
.venv/bin/python scripts/deploy_launchagent.py --profile-file config/runtime-profiles/mac-mini-before-20261007.json
sudo /usr/bin/python3 scripts/boot_services.py install --user charles
```

Pro 可用同一参数选择其保存的 balanced/performance 文件；应以实机已确认参数为准。`--profile-file auto` 恢复自动选择；不传选项时保留同机的 manual 配置。

## 验证及部署

- Python：785 passed，5 条现有 websockets 依赖弃用警告。
- 前端、日志及终端效率测试：64 passed。
- `git diff --check` 和修改源码语法检查通过。
- 主服务与手机网关 HTTP 200；主服务 `status=ok, connected=true`。
- 队列 11 条任务，10 条有效指标，1 条未知，`status=partial`、`loading=false`，与部署前的数据完整性一致。
- 部署后完整队列本地 HTTP 请求中位数 2.518 ms，状态接口 2.986 ms。仅是当次观测，受负载与网络环境影响。
- 五个系统级 launchd 服务均处于 loaded；运行代码与工作区的 main.py、runtime_profile.py、app.js 字节一致。
- 运行目录：`/Users/charles/Library/Application Support/CharlesCloudMonitor`。
- 部署前代码与参数副本（不包含凭据或数据库）：`/Users/charles/Library/Application Support/CharlesCloudMonitor-before-efficiency-20261007-r_dxar53`。
- 原 mini profile 归档：`runtime-profile-history/a09c05ee207ef30ba8960ada8e837d0d137e585ec676f8b61e8d75af2c02126d.json`，已核对与原文件逐字节相同。

现有工作区的其他改动未回退；本轮部署前后生产源码差异仅为 main.py、runtime_profile.py 和 app.js。
