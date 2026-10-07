# MacBook Pro 实机检查与参数优化 prompt

把下面内容交给 MacBook Pro 上、能够访问本项目和本机终端的编码助手。GitHub 分支：`codex/mac-runtime-tuning`。

```text
请在这台 MacBook Pro 上检查并优化 server_monitor 的机器部署参数。Mac mini 和 Pro 共用代码，本次以 Pro 的实测结果为准，保留两机独立配置。

1. 先检查 Git 状态，再 fetch origin 的 codex/mac-runtime-tuning 分支，阅读 docs/mac-runtime-profiles.md 和 docs/2026-10-07-mini-scheduling-results.md。保留本地未提交改动和 Pro 已有业务/IDE 修复；不要直接用较旧分支全目录覆盖生产代码。只整合本次参数相关代码及必要依赖。
2. 在修改或部署之前，找到实际运行目录、Python、launchd domain/label/完整 plist、runtime-profile.json（如果存在），逐字节备份并记录 SHA256。仓库内 macbook-pro-*-baseline.json 只是旧代码预设，不是这台 Pro 的实机备份。若没有 profile，记录实际硬编码参数，不编造实测配置。备份含凭据的原始文件仅保留本机，不提交 GitHub。
3. 读取真实机型、芯片、性能核/能效核、内存压力、进程架构、供电及低电量模式、温度/性能限制、服务 CPU/内存与磁盘位置。检查 launchd ProcessType、实际 spawn type 和进程优先级，先确认瓶颈，不因同为 M4 就套用 Mini 参数。
4. 使用 scripts/mac_scheduling_benchmark.py 对 Background、Standard、Interactive 做至少 5 轮交错对照。该工具使用已部署代码、临时 launchd 作业和合成数据库，不会自动部署或查询云端。若本机运行目录或 Python 不同，先适配工具路径，保证基准不访问或写入生产数据库。分别测量真实状态、历史、队列和终端目标读取的延迟，区分本地处理、云端等待与后台采集负载；只做只读查询，不执行云端任务操作或远程 shell 命令。
5. 优先针对 Pro 的调度、并发、会话容量、目标缓存及轮询参数。每次只改变一个变量，比较前后延迟、资源占用、错误率与数据新鲜度。保留现有云请求上限和指标预留槽位，不盲目增加 workers/并发；不关闭系统节能、温控或安全保护。若接电与电池情况不同，记录结果和取舍，避免为跑分牺牲日常续航。
6. 有可重复收益才应用 Pro 专属配置，保存为 config/runtime-profiles/macbook-pro-current.json，并保留实机原始备份。通过 manual 配置或精确机器匹配避免后续部署覆盖。不要修改 Mini 配置文件或让 Pro 参数作用于 Mini。若没有证据证明更好，保留现状并报告。
7. 完成相关测试后部署到本机 Pro。修改 launchd plist 后需要卸载并重新加载，等待旧进程和端口释放，并处理短暂注册冲突；仅 kickstart 不会重新读取 plist。注意 boot_services.py rollback 会恢复最初迁移前配置，不等于本次参数回退。
8. 部署后核对运行 profile、实际调度分类、主服务和手机网关健康状态，并复测相同负载。发现退化就回退。最终给出原参数与新参数、真实硬件信息、前后测量及边界、回退路径；不把模拟或单次波动称为实际整站提速。

请直接完成检查、对照测试、必要修改和本机部署；仅在缺少必需信息或权限时询问。不要修改其他机器或自动上传本机凭据。
```
