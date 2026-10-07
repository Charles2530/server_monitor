# 多台 Mac 共用代码、独立部署参数

`server/runtime_profile.py` 管理启动时读取的配置。`scripts/deploy_launchagent.py` 检测当前硬件、保存参数，并把 `web_process_type` 写入主服务 launchd plist。应用启动后在 `/api/state` 的 `runtime_profile` 返回加载的参数；该字段表示所选配置，实际调度仍要用 `launchctl print` 验证。

本次发布包括参数选择与备份、采集/指标/队列并发接入、终端会话和目标缓存接入、Mini 专用轮询及调度、离线基准工具。终端缓存按站点、任务、登录会话和是否含内部地址隔离；重叠查询合并、失败刷新失效、返回深拷贝，并限制完成缓存条目数。没有包含其他会话的页面、改名或 IDE 修改。

## 参数

| 字段 | 范围或选项 | 使用处 |
| --- | --- | --- |
| collector_concurrency | 1–6 | 每个 Collector 的云请求并发 |
| metric_concurrency | 1–4，且不大于 collector_concurrency − 2 | 指标请求，保留资源查询槽位 |
| queue_concurrency | 1–8 | 利用率队列的任务处理并发 |
| terminal_max_sessions | 1–16 | 终端会话上限 |
| target_cache_seconds | 2–30 | 终端目标结果复用时间 |
| state_poll_seconds | 2–30 | 稳定状态轮询，缺省 10 秒 |
| busy_state_poll_seconds | 2–10 | 采集中轮询，缺省 10 秒 |
| error_state_poll_seconds | 2–30 | 失败轮询，缺省 10 秒 |
| web_process_type | Background / Standard / Interactive | 主服务 launchd 调度，缺省 Background |

已实测的 Mac16,10 保持 `web_process_type=Interactive`、轮询 30/10/15 秒。Mac16,12、arm64、10 核、16 GiB 的实测 Air 使用 Standard 和原有队列并发 3，轮询仍为 10/10/10 秒。其他配置保留既有缺省策略。完整证据和限制见 [Air 实测结果](MacBook-Air-运行调优结果-1007-1317.md)。

`config/runtime-profiles/` 中 Mini 文件注明了已部署参数及历史备份。`macbook-air-current.json` 是实测 Air 的手动配置。两份 `macbook-pro-*-baseline.json` 是原代码预设，未读取 Pro 实机，不应把它们当作实际备份。Pro 应先备份实际配置，再按测量保存 `macbook-pro-current.json`；具体执行 prompt 见 `docs/macbook-pro-tuning-prompt.md`。

## 选择、保留与回退

用户级部署支持：

```sh
.venv/bin/python scripts/deploy_launchagent.py --profile-file config/runtime-profiles/macbook-air-current.json
```

上述命令仅为 Air 的用户级部署示例；其他机器使用自己的已测配置。完整部署会同步 server/static，更新这台保留历史部署差异的 Air 前须先阅读实测报告中的选择性部署说明。

显式文件会标记为 manual；在硬件信息相同的后续部署中，不传参数会保留它。`--profile-file auto` 恢复硬件自动选择。旧手动配置没有 web_process_type 时继续使用 Background。不同内容的旧运行 profile 会原样归档到运行目录下 `runtime-profile-history/<SHA256>.json`。

系统级服务不能直接运行用户级部署覆盖。按既有迁移流程先检查并备份系统配置，或限定修改实际系统 plist 和必要文件；修改后必须卸载并重新加载，等待旧进程/端口退出。`kickstart` 只重启现有注册作业。`boot_services.py rollback` 恢复的是最初迁移前的配置，应与本次调参回退区分。

本功能不会改变供电、低电量或温控设置，不会固定 CPU 核心。Interactive 是应用级调度，不表示独占性能核，也不保证云端网络提速。Air 与 Pro 的电池场景均需另外测量，不能从接电短时结果推断续航。

## 验证工具

```sh
.venv/bin/python -m scripts.mac_scheduling_benchmark --rounds 5
.venv/bin/python -m scripts.terminal_target_benchmark
PYTHONPATH=. .venv/bin/pytest -q tests
node --test scripts/frontend_efficiency_check.js scripts/terminal_efficiency_check.js scripts/logs_efficiency_check.js
```

调度基准依赖当前用户的 GUI launchd domain，默认已部署位置为 `~/Library/Application Support/CharlesCloudMonitor`；使用该目录的 Python 和代码，在临时目录运行已有离线负载，不重启实际服务，不启动云端采集。若运行目录不同，先适配路径，不应为跑基准覆盖现有部署。

Mini 原始对照数据随附于 `docs/benchmarks/`，结论和局限见 `docs/2026-10-07-mini-scheduling-results.md`。不同代码版本及任务数量的结果不能直接横向当作机器性能差异。
