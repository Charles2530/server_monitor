# server_monitor

个人云 GPU 任务监控与管理面板。接入茅山王和 SenseCore，在本机查看资源余量、运行任务、GPU 指标和容器日志，并创建、停止 ACP 训练任务。

后端使用 FastAPI、SQLite 和 Playwright；前端为原生 JavaScript、Tabler 和 ECharts。静态资源随仓库提供，默认只监听 `127.0.0.1:8766`。面向当前个人工作空间，运行监控与写操作的任务名称须包含 `charles`。

## 目录

- [取得仓库](#取得仓库)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [常用命令](#常用命令)
- [页面与功能](#页面与功能)
- [ACP 任务管理](#acp-任务管理)
- [双站点与配置](#双站点与配置)
- [监控与日志口径](#监控与日志口径)
- [macOS 自动启动](#macos-自动启动)
- [本地数据与安全边界](#本地数据与安全边界)
- [本地 API](#本地-api)
- [开发与验证](#开发与验证)
- [常见问题](#常见问题)
- [依赖与许可](#依赖与许可)
- [已知限制](#已知限制)

## 取得仓库

本仓库为 **Private**。克隆前需要已获授权的 GitHub 账号，并配置对应 SSH 密钥。

```bash
git clone git@github.com:Charles2530/server_monitor.git
cd server_monitor
```

这是个人私有仓库，按当前使用方式同时保存代码、锁定依赖和实际 `.env` 配置。获得仓库权限的机器可直接使用其中的 IAM 配置；登录会话在新机器上自动生成。虚拟环境、数据库、日志、截图和本机生成的 LaunchAgent 配置不入库，安装时按新机器重建。

## 目录结构

```text
server_monitor/
├── setup.sh                # 创建 .venv 并安装锁定依赖
├── run.sh                  # 前台运行，监听 127.0.0.1:8766
├── start.sh                # 启动 LaunchAgent 或本机后台进程
├── status.sh               # 查询服务状态
├── stop.sh                 # 停止本机监控服务
├── .env.example            # IAM 与采集配置示例
├── requirements.lock       # Python 依赖版本
├── server/
│   ├── main.py             # 本地 API、静态资源与访问边界
│   ├── auth.py             # IAM 自动登录、独立浏览器会话
│   ├── sites.py            # 两站点的工作空间、域名和数据隔离
│   ├── config.py           # 环境配置
│   ├── browser.py          # macOS Chrome / 跨平台 Chromium 选择
│   ├── collector.py        # 云接口读取、分页、任务与指标采集
│   ├── inventory.py        # 标准集群节点与总 GPU 数
│   ├── acp_actions.py      # 模板、AFS 分区、预览、创建、停止
│   ├── acp_states.py       # ACP 可停止状态
│   ├── queue_states.py     # CCI / ACP 等待状态
│   ├── task_logs.py        # Worker / 容器发现与日志读取
│   ├── normalize.py       # 字段白名单和指标单位转换
│   ├── domain.py          # 筛选、时间序列合并与统计
│   ├── service.py         # 轮询、互斥、失败恢复
│   └── store.py           # SQLite 历史与操作去重
├── static/                 # 页面、样式和前端逻辑
│   └── vendor/             # 本地 Tabler / ECharts 及许可证
├── scripts/                # 后台控制、LaunchAgent 部署、浏览器验收
├── tests/                  # 后端自动测试
├── docs/                   # 带时间戳的设计与历史验收文档
├── config/                 # 部署时生成本机 LaunchAgent 配置，不入库
└── data/                   # 本机会话、SQLite、日志与截图，不入库
```

`docs/` 和根目录的早期流程记录按各自时间保留，可能描述旧版行为。当前使用方式以本 README 和代码为准。

## 快速开始

### 1. 准备环境

支持 **macOS 和 Linux**，需要 Python 3.9+，Linux 还需 Python venv 支持。macOS 自动使用已安装的 Google Chrome；没有 Chrome 的 Mac 和 Linux 默认使用 Playwright Chromium。浏览器会话独立，不读取个人浏览器配置。

```bash
./setup.sh
```

安装会在项目 `.venv/` 内创建 Python 环境并下载锁定依赖；需要 Chromium 时同时下载浏览器到 Playwright 的用户缓存。已安装 Chrome 的 macOS 不重复下载浏览器。安装还会将实际 `.env` 的文件权限设为 600，不改系统 Python。

Linux 首次部署若缺少浏览器系统库，使用下面的命令；`--with-deps` 会通过 Playwright 安装系统软件包，可能需要 sudo。

```bash
./setup.sh --with-deps
```

Ubuntu / Debian 若连 Python venv 都没有，应先安装 `python3`、`python3-venv`。其他 Linux 发行版需自行提供对应 Python 和浏览器系统库。

### 2. 配置 IAM

仓库已包含实际 `.env`，同一账号直接使用即可。需要换账号时编辑该文件；如果没有取得实际配置，才从示例生成。

```bash
test -f .env || cp .env.example .env
chmod 600 .env
```

编辑 `.env`，填写需要接入的站点账号。

```dotenv
# 茅山王
MONITOR_IAM_TENANT=
MONITOR_IAM_USERNAME=
MONITOR_IAM_PASSWORD=

# SenseCore
SENSECORE_IAM_TENANT=
SENSECORE_IAM_USERNAME=
SENSECORE_IAM_PASSWORD=

# 采集间隔，单位秒，最低 30
MONITOR_INTERVAL=60

# auto / chrome / chromium
MONITOR_BROWSER_CHANNEL=auto
```

两个站点独立登录。只填写一个站点时，另一站会显示未连接，不影响已配置站点的采集。企业标识、用户名和密码均为对应平台的 IAM 登录信息。

### 3. 启动面板

```bash
./start.sh
./status.sh
```

浏览器打开 `http://127.0.0.1:8766`。首次采集会尝试使用本地 IAM 凭据登录；页面右上角也可重新连接。首批指标需要等待采集完成。

新机器上仅执行 `setup.sh` 和 `start.sh` 会使用普通后台进程。Linux 不调用 `launchctl`；macOS 若需要登录后自动启动，再执行[自动启动部署](#macos-自动启动)。

## 常用命令

以下命令在仓库根目录执行。macOS 和 Linux 使用相同的安装、启动、状态和停止命令；只有 LaunchAgent 部署限 macOS。

| 操作 | 命令 |
| --- | --- |
| 安装依赖 | `./setup.sh` |
| 启动后台服务 | `./start.sh` |
| 查看服务状态 | `./status.sh` |
| 停止本机监控服务 | `./stop.sh` |
| 前台调试 | `./run.sh`，Ctrl+C 退出 |
| 安装 / 更新 macOS 自动启动副本 | `.venv/bin/python scripts/deploy_launchagent.py` |
| 后端测试 | `.venv/bin/python -m pytest tests -q` |

**`stop.sh` 停止的是本机监控服务，不会停止云端训练任务。** 停止云任务需在页面中对目标 ACP 任务执行“停止”。

前台与后台不要同时启动。macOS 若已安装 LaunchAgent，`start.sh`、`status.sh` 和 `stop.sh` 优先管理该服务；否则使用项目 `data/server.pid` 管理普通后台进程。

## 页面与功能

| 页面 | 内容 |
| --- | --- |
| 资源总览 | 标准资源池可用 GPU、CPU、内存，已接入集群的节点健康与总 GPU 数 |
| 运行任务 | 本人名称包含 `charles` 的运行中 CCI / ACP 任务、指标曲线、日志及详情右上角的云任务跳转 |
| ACP 任务 | 配置模板、节点数、每节点 GPU 数、启动命令、集群，预览后创建 |
| 等待队列 | 当前工作空间所有用户的 CCI / ACP 等待、初始化与启动中任务 |
| 存储管理 | 两站点的 AFS 卷容量、分区、状态、到期时间及 charles 最近挂载记录 |
| 任务历史 | charles 的成功、失败、已停止任务列表，支持筛选、分页和跳转云控制台，无历史曲线 |

两站点的标准集群展示和可用资源汇总均忽略 `Public AEC2`。

左下角切换 `maoshanwang` / `sensecore`。URL 参数 `?site=sensecore` 可直接选择站点；刷新和浏览器前进、后退保留站点与页面。

运行列表使用平台“我的应用 / 我的任务”接口，再对显示名称进行不区分大小写的包含式匹配。等待队列使用全用户接口；其他用户的任务和 CCI 队列只读，不开放停止或日志操作。

存储管理通过 `#storage` 进入，支持分区、名称 / ID / 路径搜索与仅显示 charles 挂载记录。进入页面读取，停留时每 60 秒刷新，容量缓存 30 秒；较慢的历史挂载扫描在后台完成，完成前先显示容量和加载提示，挂载记录缓存 5 分钟。离开后停止页面轮询。容量为整个共享卷的用量，按 1000 进制显示 GB / TB，未知信息不作零处理，读取失败保留旧数据并提示。挂载关系读取当前工作空间本人 charles CCI / ACP 任务（包括历史任务），按服务类型、存储子目录和容器路径保留最新记录；这不等于当前正在使用的挂载。此页只读，不提供创建、删除或扩容操作。

任务历史通过 `#history` 进入。成功和失败使用平台最终状态；已停止任务必须有有效开始时间或本机曾观测到运行的证据，没有运行依据的记录会过滤并显示数量。CCI 历史接口没有开始时间且停止后不保留实例，旧 CCI 任务无法确认曾运行时暂不列入；后续本机运行观测标记长期保留，不随 7 天指标清理。CCI 的最近更新时间不会当成准确停止时间。任务列表按结束时间排序，缺少结束时间时使用最近更新时间；两个站点独立读取，云端失败保留旧结果并提示。

## ACP 任务管理

### 创建流程

1. 选择站点；SenseCore 先选择 AFS 分区。
2. 填写包含 `charles` 的显示名称、节点数和每节点 GPU 数。
3. 编辑启动命令，选择“自动选择”或指定兼容集群。
4. 点击预览，核对模板、集群、卡数、CPU / 内存、完整命令和排队预期。
5. 确认提交。任务可能进入初始化、运行或等待配额状态。

总卡数等于节点数 × 每节点 GPU 数。当前只支持单 `Worker` 角色的可复制模板；镜像、挂载、环境变量、优先级等沿用模板，不支持的模板会明确拒绝。

茅山王优先选择最近创建、显示名称包含 `charles` 的运行中任务作为模板；没有运行任务时使用最近的本人 charles 任务。自动资源选择依次检查 `modeltoolchain-test`、`modeltoolchain`、`vigen`，同时检查 GPU、CPU 与内存余量。

手动指定集群时，只在该集群中匹配规格；资源不足仍在该集群排队，不会自动改选其他集群。下拉列表只提供当前有效、与模板挂载兼容的标准 GPU 集群。资源余量是读取时的快照，不代表调度或预留保证。

### 启动命令

界面默认读取运行模板的启动命令；没有运行模板或脚本为空时使用 `sleep inf`。后台刷新不会覆盖正在编辑的文本，可以手动恢复默认命令。

提交时空命令转换为 `sleep inf`；非空命令末尾追加独立的 `sleep inf`，已有该末行时不重复追加。

这意味着**训练代码退出后，云任务通常仍会保活并占用资源**。需要释放 GPU 时应明确停止云任务。若脚本包含 `exit` 或 `exec` 等提前结束 shell 的行为，末尾的 `sleep inf` 可能不会执行。

### 预览、停止和异常恢复

创建预览有效期为十分钟，保存在服务进程内存中；未提交的预览在服务重启后失效。

停止前会再次核对完整任务 ID、显示名称、工作空间、本人归属和云端可停止状态。提交停止只代表进入停止流程，需继续观察云端结果。项目不提供删除任务操作。

创建和停止在 SQLite 中记录操作标识，同一标识只提交一次云写请求。超时或响应丢失时，页面保留未确认操作入口，使用原标识读取云端结果核对。不要为恢复未知结果重复创建新任务。

## 双站点与配置

| 项目 | 茅山王 | SenseCore |
| --- | --- | --- |
| 站点 ID | `maoshanwang` | `sensecore` |
| 默认工作空间 | modeltoolchain | 上海一区 ModelToolChain |
| IAM 变量前缀 | `MONITOR_IAM_` | `SENSECORE_IAM_` |
| 会话与历史目录 | `data/` | `data/sensecore/` |
| 创建模板 | 最近运行模板优先 | 按 AFS 分区分别选择最近完整模板 |

SenseCore 按本人 charles 历史任务中的完整 AFS 挂载配置归组，每个分区分别继承镜像、挂载和其他配置。两个站点的创建页面与预览均展示模板日期或来源、镜像、AFS 卷及目录映射；maoshanwang 保持原有模板选择规则。

集群与 AFS 的兼容性从平台 `crossAttach` 规则读取，不能仅凭计算区与存储区名称是否相同判断。分区切换保留当前网页内的分区草稿；配置读取失败会禁止创建，迟到的请求不会覆盖新分区选择。

工作空间与域名配置在 `server/sites.py`。茅山王工作空间可通过 `MONITOR_WORKSPACE` 覆盖；SenseCore 当前在代码中固定配置。它们不是自动发现所有账号和工作空间的通用云管理入口。

修改配置后需重启服务。使用 LaunchAgent 时，应从源码目录重新执行部署脚本，让运行副本同步配置。

`MONITOR_BROWSER_CHANNEL=auto` 使用上述默认选择；设为 `chromium` 可强制使用 Playwright 浏览器，设为 `chrome` 则要求已安装 Google Chrome。更换选项后重新运行 `setup.sh`。

远程 Linux 默认仍只监听本机。可在自己的电脑使用 SSH 隧道访问，不必把服务暴露到公网：

```bash
ssh -N -L 8766:127.0.0.1:8766 user@your-linux-server
```

然后在本机浏览器打开 `http://127.0.0.1:8766`；如果本机已有面板占用该端口，可将隧道左侧端口改为 18766。

## 监控与日志口径

- 后台默认每 60 秒采集，慢请求不重叠；前端每 10 秒读取本地快照。采集失败保留旧数据，并显示旧时间与来源错误。
- CPU、GPU、内存、显存比例统一换算为百分比。GPU 利用率与显存占用率分别展示，不混为一个指标。
- CCI 使用应用平均曲线；ACP 保留每个 Worker 的曲线，卡片取最新 Worker 值的算术平均，不相加，也不按不同硬件规格加权。
- 指标缺失或超过五分钟未更新会标记部分失败。首次采集补入平台最近一小时数据，之后在本地积累，最多保留七天；没有采集到的时段不补造数据。
- 资源池“可用 GPU”来自工作空间标准配额，“总 GPU”来自已接入集群的节点规格汇总，二者口径不同，不通过相减推算占用。当前节点总量重点覆盖茅山王 `modeltoolchain` 与 `modeltoolchain-test`。
- 日志支持本人 charles 的 CCI / ACP，按 Worker 或容器实例、主容器或初始化容器选择。每次最多读取最近 1000 条，文本最多保留末尾一百万字符，过长时提示截断。
- 仅打开日志页时请求日志；离开页面或切换对象会停止当前读取。日志按纯文本显示，移除终端控制序列，不执行其中的 HTML 或命令，也不写入本地历史库。

## macOS 自动启动

### 安装与更新

此节仅适用于 macOS。Linux 的 `start.sh` 使用可脱离终端运行的后台进程；尚未自动安装 systemd 服务，机器重启后需重新启动。

完成 `.env` 配置后运行。若已通过 `run.sh` 前台启动，先在原终端按 Ctrl+C；普通后台运行可按以下步骤切换：

```bash
./stop.sh
.venv/bin/python scripts/deploy_launchagent.py
```

部署前会确认 8766 端口已释放；若仍被占用会报错退出，避免把旧进程误判为新部署成功。

部署脚本把 `server/`、`static/`、锁定依赖和本机 `.env` 同步到以下目录，创建独立 Python 环境，并安装用户级 LaunchAgent。

```text
~/Library/Application Support/CharlesCloudMonitor/
~/Library/LaunchAgents/charles.server-monitor.plist
```

运行副本放在 Application Support，以避开 macOS 对后台进程读取桌面 / 文稿目录的限制。登录后服务自动启动，进程退出后由 launchd 重新拉起。

部署会安装 / 核对运行副本依赖并重启本监控服务，网页可能短暂断连。已有运行会话与数据库会保留；仅在目的文件不存在时导入源码目录的初始数据。它不会创建、停止或删除云任务。

部署后，源码目录 `data/` 与运行副本 `data/` 不再是同一份数据。实时会话、历史和日志以运行副本为准。修改源码或 `.env` 后，重新执行同一部署命令即可更新。

### 暂停与取消自动启动

```bash
# 暂时停止；下次登录 macOS 仍会启动
./stop.sh

# 取消后续登录自动启动，保留配置和数据
mv "$HOME/Library/LaunchAgents/charles.server-monitor.plist" \
   "$HOME/Library/LaunchAgents/charles.server-monitor.plist.disabled"
```

恢复时将配置改回原名并启动：

```bash
mv "$HOME/Library/LaunchAgents/charles.server-monitor.plist.disabled" \
   "$HOME/Library/LaunchAgents/charles.server-monitor.plist"
./start.sh
```

上述操作仅影响本项目的用户级服务，不清除会话和历史。部署失败或配置变更需要回退时，恢复先前代码与本机 `.env`，再执行部署脚本。

## 本地数据与安全边界

以下 `data/` 均相对于实际运行目录。

| 路径 | 内容 |
| --- | --- |
| `.env` | 本机 IAM 凭据与配置，权限 600 |
| `data/session.json` | 茅山王登录状态与鉴权头，权限 600 |
| `data/monitor.sqlite3` | 最近快照、指标历史与操作去重记录 |
| `data/sensecore/` | SenseCore 的独立会话与数据库 |
| `data/server.log`、`data/server.pid` | 普通后台进程的日志与 PID |
| `data/launchagent.out.log`、`data/launchagent.err.log` | LaunchAgent 运行日志 |

指标历史默认保留七天；操作记录不随指标清理，以维持重启后的请求去重。不要为了清理历史直接删除整个数据库。

服务只监听回环地址，校验 Host、Origin 和写请求标记。网页不提供读取本机凭据文件或数据目录的入口，两个站点不共享登录状态、预览或操作记录。

本项目面向可信单用户本机，没有面向公网的用户认证和多租户权限系统。不要直接改为监听 `0.0.0.0` 后暴露到公网，仓库包含实际 `.env`，因此必须保持 Private；不要将其镜像、打包或转发到公开位置。运行数据和会话留在各自机器上。

## 本地 API

API 的站点由 `X-Monitor-Site` 选择，支持 `maoshanwang` / `sensecore`，缺省为前者。所有 POST 还需 `X-Monitor-Request: 1`；浏览器调用受同源检查限制。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/task-history` | 后台读取当前站点的已结束任务列表、读取进度和过滤数量 |
| GET | `/api/storage` | 当前站点 AFS 存储卷、容量和 charles 最近挂载记录，只读 |
| GET | `/api/state` | 当前采集快照、任务、资源和队列 |
| GET | `/api/history?task_id=ACP:...&hours=1` | 已采集的历史指标，1–168 小时 |
| POST | `/api/refresh` | 请求后台刷新，异步返回 |
| POST | `/api/connect` | 请求该站点自动登录，异步返回 |
| GET | `/api/acp/options` | 模板、集群、卡数与分区选项，可传 `partition_id` |
| POST | `/api/acp/preview` | 预览配置；必填名称、节点数、每节点卡数、命令，可选集群和分区 |
| POST | `/api/acp/create` | 提交已生成的 `preview_id` |
| POST | `/api/acp/stop` | 使用 `task_id`、`display_name`、`request_id` 停止 ACP |
| GET | `/api/logs/targets?task_id=ACP:...` | 查询 Worker / 容器 |
| GET | `/api/logs?task_id=ACP:...&worker=...&container=...&tail=1000` | 读取日志快照 |

只读调用示例：

```bash
curl --noproxy '*' -fsS \
  -H 'X-Monitor-Site: sensecore' \
  http://127.0.0.1:8766/api/state
```

独立命令行工具 `cloud-task` 位于另一个仓库 `initial/tools/cloud-task/`，不是本项目的安装依赖。本项目提供网页和本地服务，两者不要求同时运行。

## 开发与验证

2026-09-29 发布前，在不复用原项目虚拟环境和数据目录的环境中完成以下验证：

| 环境 | 已验证内容 |
| --- | --- |
| macOS / Python 3.9.6 | 全新安装、两站点 IAM 登录、HTTP 服务启动；119 项后端测试通过 |
| Debian 12 ARM64 容器 / Python 3.11 | 全新安装及浏览器系统依赖、Chromium 启动、后台启动 / 状态 / 停止、停止后端口释放；119 项后端测试通过 |

Linux 验收覆盖上述容器环境；其他发行版、CPU 架构和 Python 版本未逐一实机验证。

后端测试使用本地测试数据与模拟接口：

```bash
.venv/bin/python -m pytest tests -q
```

前端语法检查需要 Node.js，仅用于开发，不是网站运行依赖：

```bash
for file in static/*.js; do
  node --check "$file"
done
```

以下浏览器回归脚本启动临时本机静态服务，并模拟云 API，不会创建或停止真实云任务：

```bash
.venv/bin/python scripts/v2_browser_smoke.py
.venv/bin/python scripts/command_logs_smoke.py
.venv/bin/python scripts/partitions_smoke.py
.venv/bin/python scripts/storage_smoke.py
.venv/bin/python scripts/history_smoke.py
```

其他 `*_smoke.py` 包含对已运行面板和实时快照的验收，需要服务已启动及对应任务 / 集群数据就绪。运行前先查看脚本中的数据前置条件。截图等产物写入本机 `data/`，不提交到 Git。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 页面打不开 | 运行 `./status.sh`；确认 8766 端口没有被其他程序占用，查看对应运行日志 |
| 自动登录失败 | 检查该站点 `.env` 三项 IAM 配置、浏览器安装和网络；点击重新连接。MFA / 验证码不会被绕过 |
| 改了代码但页面没变化 | 若使用 LaunchAgent，重新运行部署脚本，单改源码不会更新运行副本 |
| 任务正在训练但不在运行列表 | 检查站点、本人归属、云状态是否为 RUNNING，以及名称是否包含 charles |
| GPU 显示未知或旧数据 | 查看指标时间与来源错误；缺失不按零处理，刚启动的 Worker 可能尚无新采样 |
| 预览已过期 | 重新读取配置并预览；已提交但结果未知的操作应先用原标识核对 |
| 训练已结束但 GPU 仍被占用 | 启动脚本可能停留在 `sleep inf`；核对任务 ID 后明确停止云任务 |
| AFS 分区或集群不可选 | 检查模板的完整 AFS 配置与平台跨区挂载规则；不要手工绕过兼容性校验 |

## 依赖与许可

Python 依赖版本由 `requirements.lock` 锁定，主要依赖为 FastAPI、Uvicorn、HTTPX、Playwright 和 python-dotenv。

前端使用本地打包资源，保留上游许可证：

| 组件 | 许可文件 |
| --- | --- |
| Tabler | [MIT](static/vendor/tabler-LICENSE) |
| Apache ECharts | [Apache-2.0](static/vendor/echarts-LICENSE) |

## 已知限制

- 当前仅面向已有固定站点和工作空间配置，云平台内部接口变更可能需要维护。
- macOS 提供 LaunchAgent 登录自动启动；Linux 支持普通后台运行，未提供自动安装 systemd 的流程。Windows 原生环境不支持，可使用 Linux 环境。
- 电脑休眠、关机或服务停止时不继续采集；macOS 用户级 LaunchAgent 在退出登录后也会停止，历史曲线可能存在空档。
- 不提供短信 / 邮件告警，也不根据 GPU 利用率或训练日志自动停止任务。
- 不支持 CCI 创建 / 停止、不支持删除云任务，不支持任意多角色模板复制。
- 运行状态不等于训练成功，资源快照不等于预留承诺；实际调度与最终任务状态以云平台为准。
