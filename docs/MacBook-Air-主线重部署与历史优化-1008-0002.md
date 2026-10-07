# MacBook Air 主线重部署与历史优化

记录时间：2026-10-08 00:02:28（Asia/Shanghai）。本轮开始于 2026-10-07，跨日完成于 2026-10-08。

已 fetch 并确认 main 原为 `f35224a`，本地干净且无需快进；按用户选择不合并 `codex/new-feature-polish`。先重新加载 main，再部署已推送的优化提交 `c250e878bf00e95475e0dee2ae1eaf7523b396d3`。运行代码与仓库 server/static 逐字节一致。

## 实际修改与机器参数

`server/main.py` 的历史列表路由直接返回已规范化数据的 JSONResponse，避免框架重复遍历；序列化位于原有异常处理内。没有缓存历史响应，刷新状态、站点隔离和 no-store 保持有效。`tests/test_runtime_efficiency.py` 增加两站点、Unicode/空值、告警、非修改性及异常脱敏的 6 个测试。

本机为 MacBook Air Mac16,12，M4、4P+6E、16 GiB、ARM64。本轮接电测量。所有机器参数原值与新值相同：collector/metric/queue 并发 6/4/3，终端上限 8，目标缓存 8 秒，状态/忙碌/错误轮询 10/10/10 秒，launchd ProcessType=Standard、实际 spawn type=daemon、PRI=20、nice=0。未重复上轮调度类别实验。

Air 仍使用 `config/runtime-profiles/macbook-air-current.json` 与 `deploy-air.sh`，硬件匹配规则和后续手动配置保留逻辑未变。通用历史响应优化归入同一 main；Mini 配置无修改。本轮保留实机 `.env`、完整 plist、运行 profile、依赖及当前数据库/会话。Air profile SHA256 为 `91e233b4dd06ebed94c1bc35ad1a6b4dfb52537de217e81f2e4db4f2ff5e500b`。电池低电量模式 1、接电 0，未修改节能或温控设置。

## 测量与收益范围

真实已规范化历史数据，本地 ASGI HTTP 全链路五轮交错对照，每轮每版本 20 次，另有预热轮；两版本响应字节完全一致：

| 数据集 | 历史条数 | 旧处理 ms | 新处理 ms |
|---|---:|---:|---:|
| maoshanwang | 180 | 5.636 | 2.24 |
| sensecore | 216 | 6.648 | 2.628 |

表中为五轮中位数的中位数。对照使用相同快照，排除云端读取和浏览器渲染；本地处理下降约 60%，不能称作整站提速。测试套件结束后单独重做该对照，避免同时运行测试的干扰。

实际 launchd 服务重部署前后，每接口交错采样 20 次、预热后取中位数：

| 接口 | 修改前 ms | 修改后 ms |
|---|---:|---:|
| 状态 | 7.12 | 7.377 |
| 单任务曲线 | 9.53 | 9.68 |
| 历史列表 | 23.122 | 15.439 |
| 队列 | 5.904 | 5.964 |

实际历史列表从 23.122 降至 15.439 ms（约 33%），每组均无错误。该生产前后测量含正常后台采集与页面检查，并非严格隔离的五轮生产交错试验；其他接口轻微波动不归因为优化。两组之间云端采样与任务曲线内容会变化。

另测试了静态文件 gzip 1/3/5 级压缩。1 级虽减少约 14 ms 压缩 CPU 时间，但同组资源增加约 106 KB（19%）传输体积；未证明页面整体收益，保留 5 级。

部署后 60 秒资源采样：CPU 中位 0.1%、峰值 11.1%，RSS 中位 109.16 MiB、峰值 109.16 MiB。资源样本是健康观察，不是前后能耗对照；没有证明电池续航改善。系统热状态与内存原始摘要见测量 JSON。

## 功能检查与未覆盖项

- 完整 Python 测试 **843 通过**，5 条已有 websockets 弃用警告；JavaScript **64 通过**。独立审查未发现需修改问题。
- 历史浏览器专测通过懒加载、分页、筛选、空状态、错误恢复、安全名称、两站链接和移动布局。
- 部署前后实际浏览器均通过两站 7 个主要页面、任务筛选、曲线和 390px 布局，无页面 JS/HTTP 错误。终端/IDE 缺失任务参数入口正确处理；本轮没有重新建立云端终端或 IDE 工作台。
- 本轮 72 个实际读取中 66 个 HTTP 200；6 个历史重启准备请求针对运行中任务返回预期 409 `restart_state_changed`，相应释放重启准备接口返回 200。没有提交任务创建、停止、重启或改名。
- 8 个运行任务日志目标及日志读取均成功，确认 `46d28ff` 的 SenseCore 区域地址修复实际生效。SenseCore 日志当前为空、约 31 秒等待；茂山网 ACP 返回 10 条，约 9 秒等待。这是云端读取耗时。
- 最终两站监控均 connected/status=ok，各 4 个任务；历史 180/216 条且来源 ok，存储 6/38 卷且状态 ok；TokenHub 账号、额度、订阅和统计均 ok，密钥列表、趋势及用量读取通过。
- 队列 11/16 条，部分 GPU 指标缺失或过期，正确返回 partial/未知，不代表所有云指标完整。
- **SenseCore IDE 首次安装仍未完成验收**；不重复长时间安装。长期续登、真实破坏性任务操作、实体手机与电池能耗也未在本轮验证。

本报告更新此前 `MacBook-Air-功能验收报告-1007-1531.md` 的验收结论。旧报告记录的是更早检查点，其中 834 项测试、47 个累计文件和旧代码提交不代表最终部署；此前日志复核证据缺口现已用本轮 8 项实际读取补齐，保留旧报告作为历史，不将旧日期结果当作当前验证。

## 备份与回退

本轮第一次 main 重载备份为 `function-audit-20261007-235153`；应用历史优化前的新备份为：

`/Users/charles/Library/Application Support/CharlesCloudMonitor-backups/function-audit-20261007-235837`

部署归档 SHA256：`fccde86a20a26b9b1a0d2e689b7f1c5768e65669e57285327eda83dac3391fd8`。完整 SHA256SUMS 校验通过；单文件回退已在临时目录演练，无需读取或替换实机业务数据。

本轮仅修改部署的 `server/main.py`，通过 bootout 等待旧 PID、job 和端口退出后再 bootstrap，健康检查通过。回退到此次优化前运行代码：

```sh
"/Users/charles/Library/Application Support/CharlesCloudMonitor/.venv/bin/python" "/Users/charles/Library/Application Support/CharlesCloudMonitor-backups/function-audit-20261007-235837/rollback.py"
```

脚本校验当前代码仍等于本次部署，若后续修改过则拒绝覆盖；恢复代码及原完整 plist，保留当前数据库/会话与 Air profile，并检查服务健康。回退时会短暂停止本机网页服务，现有网页终端/IDE 连接可能断开。备份、会话、配置和原始业务数据仅保存在本机，本次 GitHub 只提交代码、测试和脱敏测量。

[脱敏测量证据](benchmarks/macbook-air-history-refresh-1008-0002.json)；本机详细检查日志位于 `data/air-refresh/`。
