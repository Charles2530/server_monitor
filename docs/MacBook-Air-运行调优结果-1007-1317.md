# MacBook Air 运行调优结果

生成时间 2026-10-07 13:17（Asia/Shanghai）。本机调优已部署并复测，代码用于整合 PR #1（`a6c7c99`）和远端 main 的 `5f65da4`。

## 实机与原部署

- MacBook Air，Mac16,12，Apple M4，10 核（4 性能核、6 能效核），16 GiB 内存，macOS 26.6.2。
- 运行 Python 3.9.6，进程采样确认为 ARM64；部署目录位于内置 SSD，可用空间检查时约 356 GiB。
- 当前接 65 W 电源，电量 100%。接电低电量模式关闭，电池模式开启。全程未修改系统电源、节能、温控或安全配置。
- 主服务为 `gui/501/charles.server-monitor`，监听 `127.0.0.1:8766`。原 ProcessType 为 Background，实际 spawn type 为 background，优先级 4。
- 原部署没有 `runtime-profile.json`。以下原值来自实机已部署代码与完整 plist，不使用 Pro 预设作为备份。
- 本机没有配置或运行手机网关，8767 部署前后均无监听；本次未新建网关。

## 参数决策

| 参数 | 原值 | 新值 |
| --- | --- | --- |
| launchd ProcessType | Background | Standard |
| 实际 spawn type / 优先级 | background / 4 | daemon / 20 |
| 采集并发 / 指标并发 | 6 / 4 | 6 / 4 |
| 队列并发 | 3 | 3 |
| 终端会话上限 | 8 | 8 |
| 终端目标结果复用 | 无 | 8 秒；显式刷新仍读云端 |
| 稳定 / 忙碌 / 失败状态轮询 | 10 / 10 / 10 秒 | 10 / 10 / 10 秒 |
| 后台采集周期 | 60 秒 | 60 秒 |

五轮实测支持移除 Background 限制。Standard 已取得大部分收益，Interactive 的额外改善较小且不一致；没有电池能耗测量，因此选择 Standard 并保留原并发与轮询。不能声称该选择节省了某个比例的电量。

自动策略只匹配 `Mac16,12 + arm64 + 10 核 + 16 GiB`。本机另保存 [`macbook-air-current.json`](../config/runtime-profiles/macbook-air-current.json)，标记为 manual；已验证后续同硬件部署不传参数仍保留它。Mini 的参数文件与 PR 原始字节一致，仍使用 Interactive、队列并发 6、轮询 30/10/15 秒。

## 同代码离线调度对照

使用 `scripts/mac_scheduling_benchmark.py --rounds 5`，通过已部署的原始 Python/代码创建临时 launchd 作业，以 20 个合成任务、每任务 8 个 worker、38,400 个样本比较三种 ProcessType。五轮交错，共 15 次；没有修改生产数据库或启动云端采集。每项是各轮中位数再取中位数，单位 ms。

| 策略 | 保存快照 | 状态 API | 历史 API |
| --- | ---: | ---: | ---: |
| Background | 365.883 | 4.787 | 43.890 |
| Standard | 69.008 | 0.996 | 2.607 |
| Interactive | 66.342 | 1.056 | 2.300 |

[15 次原始样本](benchmarks/macbook-air-scheduling-benchmark.json)。结果只说明这台 Air 的本地调度差异，不与 Mini 不同代码版本的数值直接比较，不代表整站或云端速度倍数。

## 实际 HTTP 复测

先保持原部署代码做 Background → Standard → Background → Standard 反向对照，再在 Standard 上部署运行参数及目标缓存。每批每个本地接口交错采样 20 次；每个阶段合并两批、每接口 40 次。预热任务历史与队列到读取完成，响应分别包含约 180 条历史任务与 11 条队列记录。使用相同本地客户端、identity 编码；P95 使用 nearest-rank。

| 接口 | 原 Background 中位数 | 仅 Standard 中位数 | 最终部署中位数 | 原 → 最终 P95 |
| --- | ---: | ---: | ---: | ---: |
| 状态 | 9.397 | 8.271 | 9.629 | 16.675 → 11.794 |
| 历史曲线（1 小时） | 45.851 | 16.367 | 18.039 | 74.173 → 40.533 |
| 任务历史 | 31.278 | 22.996 | 23.651 | 37.111 → 27.879 |
| 利用率队列 | 7.721 | 4.719 | 5.606 | 15.805 → 8.657 |

历史曲线最终中位数下降约 61%，队列下降约 27%，任务历史下降约 24%。状态接口约 9–10 ms，没有确认中位数改善；最终状态响应增加约 0.5 KiB 的运行配置说明。所有纳入统计的本地请求均为 HTTP 200。历史时间窗口及采集数据持续变化，这不是相同字节的严格微基准。

真实 CCI/ACP 终端目标各做重复读取，前后都约 3 秒，均 HTTP 200；没有证据表明云端等待得到改善。初次探索设定的 3 秒客户端超时过短，已更正为 40 秒，早期超时样本未纳入此表。重启后尚未预热的空任务列表同样未纳入此表。

[完整批次与分位数](benchmarks/macbook-air-http-measurements.json)。目标缓存另用 20 ms 模拟上游验证：8 个并发调用从 16 次上游读取减少到 2 次，CCI 批次约 88.68 → 42.99 ms，ACP 约 91.48 → 43.02 ms。它证明重复读取被合并，不是实测云端提速；[模拟原始结果](benchmarks/macbook-air-terminal-target-benchmark.json)。

## 资源与边界

最终进程 60 秒采样 CPU 中位数 0.2%，峰值 20.9%，RSS 中位数 85.42 MiB。原 Background 进程完成目标查询后 RSS 约 84.31 MiB，最终重复目标查询后约 84.20 MiB；未见持续内存增长。系统内存 free percentage 为 49%，`pmset -g therm` 未记录温度或性能警告。

单独调度阶段 60 秒样本的 RSS 曾为 47.08 MiB，但当时没有做同样的终端目标预热；不能把它与最终 85.42 MiB 直接解释成内存退化或节省。CPU 百分比是短时采样，未覆盖全天、长负载、温控或电池续航；[资源观测原始数据](benchmarks/macbook-air-resource-observations.json)。未测线程在哪类核心上运行，不宣称绑定性能核。

## 代码部署范围与验证

仓库整合完整 PR，并保留 main 在工作期间新增的页面、任务改名、编辑器恢复与实时日志提交 `5f65da4`。唯一文本冲突位于 `static/app.js` 顶部变量声明，保留了 `pollTimer` 和 `verifiedNames` 两项行为。

本机运行副本与 Git main 原本存在历史部署差异，因此本次生产部署从实机副本应用相关补丁，只修改以下九个服务端文件：`acp_terminal.py`、`cci_terminal.py`、`collector.py`、`main.py`、`runtime_profile.py`、`service.py`、`terminal_bridge.py`、`terminal_target_cache.py`、`utilization_queue.py`。其余服务端文件与全部静态文件逐字节保留。本机并非整目录同步到最新 main；本次没有把远端新页面一并部署。

完整 plist 只改变 ProcessType。每次切换都执行 bootout，等待旧 PID、launchd 注册与 8766 端口退出，再 bootstrap，未用 kickstart 替代重载。运行 profile 与仓库 Air 文件逐字节一致、权限 600；无参数重新选择验证仍保留手动值。数据库、会话、`.env` 和 Python 环境未替换。

- 完整合并树回归：820 项 Python、62 项 JavaScript 通过；Python 有 5 条既有 websockets 弃用警告。
- 实机派生候选副本：84 项配置、采集、目标读取及队列相关测试通过，并确认导入的是候选目录。
- 独立代码审查：缓存隔离、取消、配置保留及选择性部署无阻断问题。已补上坏配置 `name=[]/{}` 的类型检查及回归测试。
- 部署后按 `X-Monitor-Site` 分别检查两站，均 HTTP 200、connected=true、status=ok，快照持续更新。
- 主页、IDE、终端和关键静态脚本均 HTTP 200；未执行远程训练任务操作或端到端编辑器写入验收。
- 运行资源观察和真实 HTTP 复测已完成；本机没有手机网关，无法给出网关健康通过的结论。

直接在仓库根目录使用无范围的 pytest 会误收集本机 `data/` 内旧测试副本；本次使用 `PYTHONPATH=. .venv/bin/pytest -q tests` 验证正式测试目录。

## 原始备份与回退

本机原始完整部署归档（含本地配置、数据文件及全部部署代码，不含未变的虚拟环境）与完整 launchd plist：

`~/Library/Application Support/CharlesCloudMonitor-backups/macbook-air-before-tuning-20261007-124123`

归档 SHA256 `8d3852a3cf7ab34106a748cd25204cde3ce19173611c5a54d7f36272e8ac1695`；原 plist SHA256 `0e782f2d400bbe18529561f76ea29f017e3754954d0e119438cb50c07fe41367`。归档包含活跃 SQLite 文件，不能当成事务一致的数据库恢复包；此次回退不覆盖数据库。

精确文件回退目录：

`/Users/charles/Library/Application Support/CharlesCloudMonitor-backups/macbook-air-apply-20261007-130621`

其中 `manifest.json` 记录原始及部署后 SHA256，`SHA256SUMS` 校验备份和脚本。回退脚本已在隔离临时目录验证：恢复旧代码、删除本次新增配置/模块、恢复完整 Background plist，再等待旧进程退出并重新加载，保留当前数据库和登录会话。若目标文件在本次部署后发生变化，会拒绝覆盖并要求人工核对。

```sh
"$HOME/Library/Application Support/CharlesCloudMonitor/.venv/bin/python" \
  "$HOME/Library/Application Support/CharlesCloudMonitor-backups/macbook-air-apply-20261007-130621/rollback.py"
```

原始备份与凭据均未随本次提交上传。若希望只恢复调度，应先备份当前 plist，改变 ProcessType 后按同样的 bootout/等待/bootstrap 流程操作；不要使用 `boot_services.py rollback` 代替本轮回退。
