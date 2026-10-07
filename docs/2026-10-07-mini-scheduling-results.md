# Mac mini 调度参数优化与部署结果

本轮针对这台 Mac mini 的实际部署调度进行优化，已生效。通用压缩缓存方案已停止，没有部署相关代码。

## 本机证据与选择

- 实机：Mac16,10、Apple M4、4 个性能核、6 个能效核、24 GiB 内存。
- 检查时系统内存 free percentage 为 40%；`pmset -g therm` 未记录温度或性能警告。单次检查不能代表所有时段。
- 原主服务 `system/charles.server-monitor` 的 `ProcessType=Background`，launchd 确认 `spawn type = background`，进程优先级为 4。
- 本机 `launchd.plist(5)` 手册说明 Background 对后台作业施加资源限制；Interactive 用于响应性依赖于该服务的交互应用。本服务处理页面和终端请求，未采用 XPC 活动跟踪，因此不选择 Adaptive。
- 主服务运行目录、Python 和数据库已在内置磁盘，未把生产服务迁移到外置工作区。没有发现足以支持扩大云端并发的证据。

这类调度限制可能影响其他 Mac；本轮依据本机实测，仅对已确认型号 Mac16,10 自动启用。没有测量线程实际驻留在哪类核心，也未测量功耗，不能声称绑定性能核或节能比例。

## 相同代码与数据的调度对照

运行 `.venv/bin/python -m scripts.mac_scheduling_benchmark`。使用已部署的内置磁盘 Python 和服务器代码，临时 launchd 作业、临时 SQLite、20 个合成任务、每任务 8 个 worker、共 38,400 个样本。每种策略 5 轮，交替测试顺序；作业结束后卸载。没有启动采集器、访问云端或写入生产数据库。

下表为各轮中位数再取中位数，单位 ms：

| launchd 策略 | 重复保存快照 | 状态接口 | 历史接口 |
| --- | ---: | ---: | ---: |
| Background | 278.424 | 3.447 | 41.909 |
| Standard | 72.624 | 0.831 | 2.990 |
| Interactive | 70.140 | 0.755 | 2.766 |

原始轮次：`docs/benchmarks/mini-scheduling-benchmark.json`。这是本机离线合成负载，用于比较调度对本地处理的影响，不能换算为云端网络提速。Standard 已获得大部分收益；本次选择 Interactive 用于交互主服务，其他后台服务保持原分类。

## 线上部署前后观测

同一本地 HTTP 客户端，gzip 协商，每接口 20 次请求。没有修改静态资源、压缩代码或数据库查询。以下数据受当时系统负载和后台采集影响，不是完整网页加载时间。

| 接口 | 前中位数 | 后中位数 | 前 P95 | 后 P95 |
| --- | ---: | ---: | ---: | ---: |
| `/api/state` | 3.457 ms | 3.758 ms | 4.635 ms | 6.810 ms |
| `/static/app.js` | 5.389 ms | 3.987 ms | 6.541 ms | 5.484 ms |
| `/static/vendor/echarts.min.js` | 87.153 ms | 22.641 ms | 110.802 ms | 27.872 ms |
| `/api/history`（1 小时） | 37.602 ms | 6.283 ms | 44.500 ms | 9.238 ms |

状态接口没有可确认的线上改善；历史和较大脚本的本地服务耗时下降明显。历史内容随采集变化，前后响应分别为 5,885 / 5,883 字节，因此不能将该对比解释为严格相同字节的微基准。两个静态脚本前后字节数相同。原始数据为 `docs/benchmarks/mini-scheduling-http-before.json` 和 `docs/benchmarks/mini-scheduling-http-after.json`。

## 持久化与兼容

- 新参数：`web_process_type=Interactive`，写入 Mini 的运行 profile 和 `config/runtime-profiles/mac-mini-current.json`。
- 主系统 plist 仅将 `ProcessType` 从 Background 改成 Interactive，重新加载后确认 `spawn type = interactive`，进程优先级 31。
- `server/runtime_profile.py` 仅对 Mac16,10 自动选择该参数；`scripts/deploy_launchagent.py` 把参数写入生成的主服务 plist，迁移成系统服务时保留。
- 无该字段的旧手动配置默认保持 Background。枚举只接受 Background / Standard / Interactive。
- 两份 MacBook Pro 预设及此前 Mini 原始备份 SHA256 均未变。Pro 预设仍是此前代码配置，未读取 Pro 实机。
- Mini 旧参数原始字节另存为 `config/runtime-profiles/mac-mini-before-scheduling-20261007.json`，运行目录 `runtime-profile-history/` 也保留旧配置。
- 采集/指标/队列并发仍为 6 / 4 / 6，终端上限 8，目标缓存 8 秒，轮询仍为 30 / 10 / 15 秒。手机网关、网络和飞书服务未调整。

## 验证与回退

- Mini 当时完整工作区回归：814 passed，5 条现有 websockets 弃用警告；JavaScript：64 passed。本发布分支排除了其他功能改动，单独执行回归：768 项 Python、60 项 JavaScript 通过。
- 独立代码审查无阻断问题，另复跑 49 项配置与部署测试通过。
- 部署后的 `/api/state` 为 HTTP 200、`connected=true`、`status=ok`，手机网关 `/health` 为 HTTP 200。
- 生产 profile loader 的 SHA256 与测试源码一致；运行 profile 与 Mini 保存配置逐字节一致；系统 plist 比较确认只有 ProcessType 变化。
- 首次重新加载遇到 launchd 卸载后的短暂注册冲突，已先恢复旧配置与服务；等待卸载完成并增加有界重试后，第二次部署成功。

完整原文件与清单：
`/Users/charles/Library/Application Support/CharlesCloudMonitor-before-mini-scheduling-20261007-_68uwuqm`

清单 `manifest.json` 对应原 profile loader、运行 profile、系统 plist、项目 plist 和 Mini 保存配置；`data/mini-scheduling-deployment-backup.txt` 记录目录。回退本轮时只恢复清单列出的文件，再卸载并加载主服务。加载前等待旧进程和端口释放，不用仅能重启进程的 kickstart 代替配置重载。回退前核验没有需要保留的后续修改。

原有 `boot_services.py rollback` 会恢复最初迁移前的 Background，这是其原有回滚语义；随后运行更新后的用户部署及系统迁移，才能重新应用 Mini 的 Interactive 设置。
