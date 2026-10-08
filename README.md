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
- [macOS 无人登录启动](docs/macos-unattended.md)
- [飞书群通知](docs/feishu-notifications.md)
- [飞书监控看板](docs/feishu-bitable.md)
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
├── deploy.sh               # 按当前硬件自动选择 Air / Mini profile 并部署
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
│   ├── service.py          # 轮询、互斥、失败恢复
│   ├── terminal_resolver.py # CCI / ACP 终端目标解析基类
│   └── store.py           # SQLite 历史与操作去重
├── static/                 # 页面、样式和前端逻辑
│   └── vendor/             # 本地 Tabler / ECharts 及许可证
├── scripts/                # 后台控制、LaunchAgent 部署、浏览器验收
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
| 安装 / 更新 macOS 自动启动副本 | `./deploy.sh` |
| 本机验证（测试源码不上传 GitHub） | `.venv/bin/python -m pytest tests -q` |

**`stop.sh` 停止的是本机监控服务，不会停止云端训练任务。** 停止云任务需在页面中对目标 ACP 任务执行“停止”。

前台与后台不要同时启动。macOS 若已安装 LaunchAgent，`start.sh`、`status.sh` 和 `stop.sh` 优先管理该服务；否则使用项目 `data/server.pid` 管理普通后台进程。

## 页面与功能

| 页面 | 内容 |
| --- | --- |
| 资源总览 | 标准资源池可用 GPU、CPU、内存，已接入集群的节点健康与总 GPU 数 |
| TokenHub | 余额、花费趋势、密钥用量与当前并发、分组调整、账号额度 |
| 运行任务 | 本人名称包含 `charles` 的运行中 CCI / ACP 任务、指标曲线、日志及详情右上角的云任务跳转 |
| ACP 任务 | 配置模板、节点数、每节点 GPU 数、启动命令、集群，预览后创建 |
| 队列管理 | 上方展示所有用户的 CCI / ACP 等待、初始化与启动中任务；下方按 GPU 利用率升序展示运行中的 ACP 任务 |
| 存储管理 | 两站点的 AFS 卷容量、分区、状态、到期时间及 charles 最近挂载记录 |
| 任务历史 | charles 的成功、失败、已停止任务列表，支持筛选、分页和跳转云控制台，无历史曲线 |

两站点的标准集群展示和可用资源汇总均忽略 `Public AEC2`。

### TokenHub

资源总览增加 TokenHub 摘要，侧边栏的 TokenHub 页面提供仪表板、API 密钥、账号额度三个页签。仪表板突出余额与今日实际消费，支持切换最近 7／30 天实际花费折线图（USD），并显示账号额度速览；用量明细保留在折叠区域。账号额度使用原站独立接口，按账号显示周期使用率、可用状态及重置时间；本站账户独立于 GPU 云站点，切换茅山王／SenseCore 不会切换 TokenHub 账户。金额保留 USD 单位，与已用 token 分开展示；未读取到的数据使用“—”或“未知”，读取失败保留旧值和原更新时间。

首次在运行服务的电脑上点击“连接 TokenHub”，在弹出的独立浏览器里完成 GitHub 登录（限时 5 分钟）。登录成功后窗口自动关闭，后端每 60 秒更新摘要并在需要时刷新本站会话。`data/tokenhub/session.json` 仅保存 TokenHub access/refresh token，目录权限 700、文件权限 600，不入 Git，不保存 GitHub 密码或 Cookie。刷新失败时需手动重新连接；“断开本机连接”只删除本机本站会话，不撤销 GitHub 授权或远端 API Key。

用量按北京时间选择起止日期，最长 366 天，每页 20 条。API 密钥列表展示今日／近 30 天实际用量（四位小数）、当前并发、分组和使用时间，不返回明文 Key。点击“复制密钥”才读取该密钥的完整值并写入剪贴板；页面不展示明文，待复制值最多在内存保留 30 秒，复制成功、切换页面或隐藏页面会清理待复制内容。手机复制需要设备已获控制权限，不需要更新 APK。点击“调整分组”后选择可用分组并保存，只更新该密钥的分组；保存前会重新校验账号、密钥归属和当前分组，取消或仅选择不会写入。保存结果不确定时需刷新确认，不自动重试。创建密钥和其他账户管理仍前往原站。配对的手机可读取详情及调整密钥分组，连接和断开需在本机执行。原站登录态与本机采集会话独立，打开原站可能需要再次登录。此次改版只需更新电脑端服务，旧 APK 重新打开页面即可读取新版网页和数据；网页兼容旧 APK 注入的导航样式。为兼容旧 APK 的外链白名单，手机端原站链接改为地址复制弹窗，复制后在系统浏览器打开；电脑端仍直接跳转。手机网关只开放明确列出的查询接口、分组保存和显式密钥复制接口，不提供任意代理。

左下角切换 `maoshanwang` / `sensecore`。URL 参数 `?site=sensecore` 可直接选择站点；刷新和浏览器前进、后退保留站点与页面。

运行列表使用平台“我的应用 / 我的任务”接口，再对显示名称进行不区分大小写的包含式匹配。等待队列使用全用户接口；其他用户的任务和 CCI 队列只读，不开放停止或日志操作。

运行队列读取当前站点工作空间所有用户的运行中 ACP 任务，创建时间不限，不传最近一个月的时间条件，完整读取所有分页。“仅看我的任务”沿用运行监控的规则，按任务名称包含 `charles`（不区分大小写）筛选。表格展示创建时间及从平台实际启动时间计算的已运行时长，不包含排队时间；启动时间缺失或异常时显示“未知”。排序仍按各 Worker / GPU 最新有效采样的均值升序排列，不使用任务运行期间的历史平均值。0% 为有效值；Worker 缺失、采样不完整、相差超过 60 秒、超过 5 分钟未更新或读取失败时显示“未知”，排在末尾。进入队列管理后读取，停留时每 60 秒更新，离开或隐藏页面停止轮询；列表读取失败时显示旧列表及上次观测到的运行时长。只读展示，不保存其他用户的历史曲线。

存储管理通过 `#storage` 进入，支持分区、名称 / ID / 路径搜索与仅显示 charles 挂载记录。进入页面读取，停留时每 60 秒刷新，容量缓存 30 秒；较慢的历史挂载扫描在后台完成，完成前先显示容量和加载提示，挂载记录缓存 5 分钟。离开后停止页面轮询。容量为整个共享卷的用量，按 1000 进制显示 GB / TB，未知信息不作零处理，读取失败保留旧数据并提示。挂载关系读取当前工作空间本人 charles CCI / ACP 任务（包括历史任务），按服务类型、存储子目录和容器路径保留最新记录；这不等于当前正在使用的挂载。此页只读，不提供创建、删除或扩容操作。

任务历史通过 `#history` 进入。成功和失败使用平台最终状态；已停止任务必须有有效开始时间或本机曾观测到运行的证据，没有运行依据的记录会过滤并显示数量。CCI 历史接口没有开始时间且停止后不保留实例，旧 CCI 任务无法确认曾运行时暂不列入；后续本机运行观测标记长期保留，不随 7 天指标清理。CCI 的最近更新时间不会当成准确停止时间。任务列表按结束时间排序，缺少结束时间时使用最近更新时间；两个站点独立读取，云端失败保留旧结果并提示。

### 站内 Terminal

两站点的本人 charles 运行任务支持在监控网站内打开 CCI / ACP Shell，可选择实例、容器或 Worker。终端使用本地打包的 [xterm.js](https://github.com/xtermjs/xterm.js)，支持 ANSI 彩色输出、中文、复制粘贴、字号调整、全屏，以及手机端 Ctrl+C / Tab / 方向键。页面保留官方控制台入口。

手机首次打开默认进入运行任务；底部仍保留资源总览、TokenHub、ACP 任务、队列、存储和任务历史，电脑默认仍是全站资源总览。本人运行中的 CCI 卡片提供「打开终端」快捷入口；任务详情将资源监控、日志和 Terminal 放在同一操作栏。终端返回时恢复同一站点、任务和页签，并保留搜索、服务筛选及监控时间范围。任务已消失或暂时无法确认时回到列表提示，不自动打开其它任务。编辑器入口仍仅供电脑使用。此处保存的是导航状态，离开终端页面仍遵循下文的 Shell 关闭规则。

页面采用紧凑顶栏，连接目标收在“连接设置”中，终端随窗口占满剩余空间。连接建立时主动设置远端 PTY 行列数，窗口、字号和全屏变化后继续同步；终端旁显示当前列数 × 行数。手机使用独立的紧凑布局：顶栏保留连接状态、“键盘”和“更多”，底部快捷键可横向滑动；复制、粘贴、字号、全屏、断开及连接设置收在“更多”内。横屏与软键盘弹出时按实际可视高度调整终端；可视高度较小时，顶部栏与快捷键合并到底部单栏，键盘开关、连接状态及“更多”固定在右侧，快捷键在左侧横向滑动。当前连接目标和“返回监控”可在“更多”中找到；高度恢复后自动恢复顶栏，电脑端保留原工具栏。Ctrl 为一次性修饰键（例如点 Ctrl 后输入 b）；方向键跟随当前终端的光标模式，支持 tmux 等全屏程序。输入法候选未确认时暂停快捷键发送，避免 Enter 抢先提交。 Android 软键盘的 229 按键路径由 input 事件提交，输入法候选由 composition 路径提交，避免同一个字符由两条路径重复发送；退格、回车及候选确认后的编辑也支持只有 input 事件的键盘。一次性 Ctrl 在使用快捷键或粘贴后清除，粘贴成功会关闭工具面板并返回终端输入。

后台使用该站点的 IAM 会话连接云终端，认证过期时尝试自动续登；云端凭据不会交给终端页面。本机使用 WebSocket，旧 APK 使用即时返回输出的 HTTP 长轮询，无需重装 APK。手机设备须有写入权限；另一台 Mac 同步代码后，需重新部署主服务和手机网关（`.venv/bin/python scripts/mobile_host.py install`），才能使用新增接口。

切换窗口或网页连接暂时中断时，后台保留当前 Shell；页面自动恢复连接并从已渲染的位置继续接收输出，未确认输入不会自动重发。已使用 HTTP 兼容连接的会话在恢复时直接查询当前输入序号，不再重复尝试 WebSocket 或等待空输出长轮询。重新连接按钮优先恢复原 Shell；原会话已结束时才提示重新建立。显式断开或关闭页面会关闭当前 Shell，可能影响其中的前台命令，但不会主动停止云训练任务。输出仅在内存中暂存，每会话待读取输出上限 1 MiB；每站点最多 8 个终端会话，网页传输脱离后保留 15 分钟，会话最长 8 小时，不保存终端历史。后台重启、云端 Shell 已结束或输出积压超限后无法恢复原 Shell。

## ACP 任务管理

运行任务的 ACP 详情顶栏提供“查看配置”：只读获取当前选中任务的启动命令、镜像、副本与资源规格、集群分区及 AFS 挂载。命令支持换行显示和原文复制，不采用创建表单的最新模板；两个站点均支持。

### 创建流程

1. 选择站点；SenseCore 先选择 AFS 分区。
2. 填写包含 `charles` 的显示名称、节点数和每节点 GPU 数。
3. 编辑启动命令，选择“自动选择”或指定兼容集群。
4. 点击预览，核对模板、集群、卡数、CPU / 内存、完整命令和排队预期。
5. 确认提交。任务可能进入初始化、运行或等待配额状态。

总卡数等于节点数 × 每节点 GPU 数。当前只支持单 `Worker` 角色的可复制模板；镜像、挂载、环境变量、优先级等沿用模板，不支持的模板会明确拒绝。

茅山王优先选择最近创建、显示名称包含 `charles` 的运行中任务作为模板；没有运行任务时使用最近的本人 charles 任务。自动资源选择依次检查 `modeltoolchain-test`、`modeltoolchain`、`vigen`，同时检查 GPU、CPU 与内存余量。前两个集群还会实时读取 AEC2 节点分配，按每副本规格计算各节点可容纳的副本数，避免把分散的余卡算成一整组；异常、禁用或不可调度节点不参与计算，数据未知时跳过该优先候选。vigen 不查节点分配，仍按总配额判断。全部不满足时按原优先级排队；手动选集群、重新启动和释放重启保留指定 / 原集群，并在预览中提示资源检查结果。检查不预留资源，最终以云端调度为准。

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
- 采集落库、历史清理、曲线查询与曲线 JSON 编码在后台线程执行，避免磁盘等待和大曲线处理阻塞网页与终端连接。重复采样只有数值变化才更新，仍接收旧时间点的修正与空值；每次数据库操作结束后关闭连接，停服时等待已开始的写入完成。
- ACP 创建、停止、释放重启的操作记录，以及任务历史运行证据和手机网关鉴权，也在后台线程读写；请求取消时等待已开始的数据库操作结束，再释放操作锁，保持去重与未知结果核对机制。
- 存储容量使用共享后台刷新：首次读取最多等待 55 秒，已有快照立即返回，多窗口复用同一次刷新。失败保留旧数据和原时间，提示可能过期，30 秒后允许重试；挂载关系仍独立后台更新。
- 日志目标、任务配置/创建预览和日志正文分别设置 30、50、80 秒的读取总时限；超时取消读取并返回明确错误。创建、停止等云端写操作不使用这一读取时限，也不会因超时自动重发。
- 终端输入的 10 秒时限包含等待写锁的时间，心跳发送也有 10 秒时限；无法确认输入结果时断开并提示，不自动重新执行命令。
- 日常采集在云端按运行/排队状态过滤，减少已结束任务的分页；队列仍读取全用户数据，并另行校验本人 ACP 的操作权限。茅山王创建模板先查询运行任务，无本人运行模板才回查历史；重启与停止按任务 ID 核对。SenseCore 保留按 AFS 分区选最新模板的规则。
- 已明确为 0 卡的 CCI / ACP 仅采集 CPU、内存，不请求 GPU 面板，也不将缺少 GPU 指标计为采集失败。GPU 数量未知时仍执行完整指标检查。
- 状态接口直接过滤曲线与内部标识，曲线按需从历史接口读取。云端读取遇到超时、限流或服务端错误最多尝试三次，退避等待释放并发名额；普通 4xx 错误不重复请求，认证错误沿用自动续登流程。
- CPU、GPU、内存、显存比例统一换算为百分比。GPU 利用率与显存占用率分别展示，不混为一个指标。
- CCI 使用应用平均曲线；ACP 保留每个 Worker 的曲线，卡片取最新 Worker 值的算术平均，不相加，也不按不同硬件规格加权。
- 指标缺失或超过五分钟未更新会标记部分失败。首次采集补入平台最近一小时数据，之后在本地积累，最多保留七天；没有采集到的时段不补造数据。
- 资源池“可用 GPU”来自工作空间标准配额，“总 GPU”来自已接入集群的节点规格汇总，二者口径不同，不通过相减推算占用。当前节点总量重点覆盖茅山王 `modeltoolchain` 与 `modeltoolchain-test`。
- 茅山王上述两个集群的“整组空闲 GPU”按健康、启用、可调度节点上尚未分配的全部 GPU 统计，例如 `8 卡 × 2 组`。它不代表 GPU 利用率为零，也不保证节点 CPU、内存足够；创建预览会进一步检查实际规格。数据缺失或采集失败显示未知，不将旧空闲数或未知当作零。
- 采集器总请求并发上限为 6；任务指标和 Worker 查询最多占用 4 路，给节点总量与分配查询留出空间，避免其在本机排队期间超时而显示“未知”。
- 日志支持本人 charles 的 CCI / ACP，按 Worker 或容器实例、主容器或初始化容器选择。每次最多读取最近 1000 条，文本最多保留末尾一百万字符，过长时提示截断。
- 仅打开日志页时请求日志；离开页面或切换对象会停止当前读取。日志按纯文本显示，移除终端控制序列，不执行其中的 HTML 或命令，也不写入本地历史库。

## macOS 自动启动

默认使用登录后运行的 LaunchAgent。若需要重启后无需登录就恢复监控与手机入口，使用[系统级无人登录部署](docs/macos-unattended.md)，其中包含独立 Tailscale 进程、回退方式和真实重启验收要求。

已实测 Mac 的参数选择与保留方式见 [Mac 运行参数说明](docs/mac-runtime-profiles-1007-1327.md)，本机 Air 的前后测量与回退见 [Air 调优结果](docs/MacBook-Air-运行调优结果-1007-1327.md)。Air 与 Mini 共用同一 `main` 和完整功能，`./deploy.sh` 会读取当前硬件并自动选择对应 profile：Air 使用 Standard 与队列并发 3，Mini 使用 Interactive 与队列并发 6。其他机器继续使用保守的通用 profile。见 [Air 独立部署与回退](docs/MacBook-Air-独立适配说明-1007-1531.md) 和 [本机最新部署验收](docs/MacBook-Air-主线重部署与历史优化-1008-0002.md)。

本轮新优化合并、Air/Mini 共用功能与独立配置，以及 Maoshanwang 实机验收见 [主线集成验收](docs/Air-Mini共用主线与Maoshanwang验收-1008-0120.md)。

### 安装与更新

此节仅适用于 macOS。Linux 的 `start.sh` 使用可脱离终端运行的后台进程；尚未自动安装 systemd 服务，机器重启后需重新启动。

完成 `.env` 配置后运行。若已通过 `run.sh` 前台启动，先在原终端按 Ctrl+C；普通后台运行可按以下步骤切换：

```bash
./stop.sh
./deploy.sh
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

`/api/tokenhub/*` 为独立账户，不使用 GPU 站点请求头。它们仅连接固定 TokenHub 域名，不接受任意上游 URL。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/task-history` | 后台读取当前站点的已结束任务列表、读取进度和过滤数量 |
| GET | `/api/utilization-queue` | 所有用户运行中 ACP 任务的 GPU 利用率升序列表，只读，后台刷新与 60 秒缓存 |
| GET | `/api/storage` | 当前站点 AFS 存储卷、容量和 charles 最近挂载记录，只读 |
| GET | `/api/tokenhub/state` | TokenHub 摘要及每部分更新时间；`refresh=true` 主动读取 |
| GET | `/api/tokenhub/trend?days=7` | 最近 7 或 30 天的每日实际花费，含各类 Token 与日期范围 |
| GET | `/api/tokenhub/keys?page=1` | API 密钥元数据、今日／近 30 天实际用量及当前并发；每页 20 条，不含明文 Key |
| GET | `/api/tokenhub/usage?start_date=2026-10-01&end_date=2026-10-06&page=1` | 按北京时间日期范围读取用量明细 |
| GET | `/api/tokenhub/key-groups?key_id=1` | 当前密钥分组及该账号可选择的分组 |
| POST | `/api/tokenhub/key-copy` | 按需读取指定密钥完整值；严格校验 `key_id`、`account_id` 与归属，不缓存；手机需控制权限 |
| POST | `/api/tokenhub/key-group` | 校验账号、旧分组与目标分组后保存；请求包含 `key_id`、`group_id`、`expected_group_id`、`account_id` |
| POST | `/api/tokenhub/connect` | 本机打开独立登录窗口，异步返回；手机不开放 |
| POST | `/api/tokenhub/disconnect` | 清除本机 TokenHub 会话及缓存；手机不开放 |
| GET | `/api/state` | 当前采集快照、任务、资源和队列 |
| GET | `/api/history?task_id=ACP:...&hours=1` | 已采集的历史指标，1–168 小时 |
| POST | `/api/refresh` | 请求后台刷新，异步返回 |
| POST | `/api/connect` | 请求该站点自动登录，异步返回 |
| GET | `/api/acp/task-config` | 读取指定 `task_id` 的实际启动配置，仅限当前站点名称含 charles 的 ACP 任务 |
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

运行离线性能基准（20 个模拟任务、每任务 8 个 Worker、38,400 个采样），输出重复保存与状态接口的中位耗时：

```bash
.venv/bin/python -m scripts.runtime_benchmark
```

基准使用临时数据库，不请求云平台；结果取决于本机负载，不代表云接口延迟或长期稳定性验收。

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
.venv/bin/python scripts/task_config_smoke.py
.venv/bin/python scripts/partitions_smoke.py
.venv/bin/python scripts/storage_smoke.py
.venv/bin/python scripts/history_smoke.py
.venv/bin/python scripts/cci_terminal_smoke.py
```

手机任务连续操作可运行 `.venv/bin/python scripts/mobile_task_workspace_smoke.py`，覆盖 CCI 卡片直达终端、日志返回、两站点隔离、时间范围、全局入口、历史导航、缺失任务、横屏和旧 APK 样式兼容；全部使用本地模拟接口。

手机输入和布局回归可运行 `.venv/bin/python scripts/mobile_terminal_smoke.py`，覆盖触屏快捷键、模拟输入法、横竖屏、仅可视区域缩小的键盘场景及 HTTP 恢复后不重发输入；使用模拟接口，不访问云任务。浏览器模拟不能替代各款手机和输入法的实机验证。

终端全屏程序的回归可运行 `.venv/bin/python scripts/terminal_pty_smoke.py`，需要本机安装 tmux。脚本使用独立的本机 PTY 和临时 tmux 服务，测试初始尺寸、连接期间缩放、中文长行、全屏、字号、超宽屏、手机布局、冻结页面超过 45 秒、原 Shell 重连及输入确认丢失后不重发；结束时只清理该测试的 tmux 服务，不访问云任务。

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

Python 依赖版本由 `requirements.lock` 锁定，主要依赖为 FastAPI、Uvicorn、HTTPX、Playwright、websockets 和 python-dotenv。

前端使用本地打包资源，保留上游许可证：

| 组件 | 许可文件 |
| --- | --- |
| Tabler | [MIT](static/vendor/tabler-LICENSE) |
| Apache ECharts | [Apache-2.0](static/vendor/echarts-LICENSE) |
| [xterm.js 5.5.0](https://github.com/xtermjs/xterm.js) | [MIT](static/vendor/xterm-LICENSE) |
| xterm addon-fit 0.10.0 | [MIT](static/vendor/xterm-addon-fit-LICENSE) |

## 已知限制

- 当前仅面向已有固定站点和工作空间配置，云平台内部接口变更可能需要维护。
- macOS 提供 LaunchAgent 登录自动启动；Linux 支持普通后台运行，未提供自动安装 systemd 的流程。Windows 原生环境不支持，可使用 Linux 环境。
- 电脑休眠、关机或服务停止时不继续采集；macOS 用户级 LaunchAgent 在退出登录后也会停止，历史曲线可能存在空档。
- 不提供短信 / 邮件告警，也不根据 GPU 利用率或训练日志自动停止任务。
- 不支持 CCI 创建 / 停止、不支持删除云任务，不支持任意多角色模板复制。
- 运行状态不等于训练成功，资源快照不等于预留承诺；实际调度与最终任务状态以云平台为准。

## Android 手机客户端（可选原型）

新增单 APK 客户端与独立 macOS 手机网关，复用原监控后台。手机内嵌 tsnet，首次网络登录和设备配对后，经私有 HTTPS 访问常开 Mac。支持本机审批/撤销、读写权限及主机隔离；不自动开放公网入口。

部署、构建、真机验收与接手 AI 指令见 [手机客户端与 Mac 部署交接](docs/手机客户端与Mac部署交接-1001-1705.md)。Android 工程在 `mobile/android`，构建入口为 `./mobile/build_android.sh`；网关部署入口为 `.venv/bin/python scripts/mobile_host.py install`。先更新主监控，再安装可选网关。

当前已通过代码测试和 APK 构建，并在一加 13T 上完成扫码、配对及监控主页加载验证；切换手机移动网络后的跨网连接仍需按交接文档验收。APK、配对数据、Android SDK 与签名文件不随 Git 同步。

0.1.3 已修复 Android 原生日志目录缺失导致的启动闪退，并在一加 13T / Android 16 上验证冷启动及返回前台；详见 [启动修复与真机验证](docs/Android启动修复与真机验证-1001-1740.md)。0.1.5 补齐扫码依赖，修复点击扫描时的闪退，增加配对码过期提示、具体连接错误和只读网关检测。已在一加 13T / Android 16 上完成 Tailscale 登录、扫码配对及监控数据加载验证；移动网络下的跨网访问仍待单独验收。

0.1.7 使用网站同款图标及 `ilab server monitor` 名称。启动时恢复已有登录与配对，网络恢复期间明确保留配对状态；网络已连接或仍在启动时，登录按钮不会重复申请授权。修复首次状态刷新被忙碌标记跳过的问题，恢复完成后自动进入监控。

0.1.8 优化手机版：紧凑站点顶栏、底部六入口、资源/队列/存储/历史卡片、任务详情返回列表、可换行操作按钮及手机日志/命令/弹窗布局。连接页默认收起配对与诊断选项，保留已有登录和配对。APK 内置与网页同源的手机样式，仅用于受信任的本地监控页，现有电脑端无需先更新即可显示新版布局。已通过窄屏模拟检查、15 项 Android 单元测试，以及一加 13T 的覆盖安装、自动恢复连接和任务详情只读检查。

## CCI 站内 VS Code（电脑版）

在本人运行中的 CCI 任务详情点击「进入 VS Code」。页面内嵌 code-server，后台使用已有 IAM 会话核实任务及容器、自动准备并认证编辑器，无需再输入云平台或编辑器密码。多个实例/主容器时先选择目标。仅支持本机 HTTP `127.0.0.1` / `localhost` 桌面浏览器；ACP、手机和 APK 不显示入口，手机网关不转发此功能。

此功能还需要监控电脑已有可用的 SSH 跳板别名，且该跳板能访问 CCI 实例内网 IP。默认 Maoshanwang 使用 `maoshanwangtech`，SenseCore 使用 `sensecore`；可在 `.env` 中设置 `MONITOR_CCI_IDE_SSH_HOST` / `SENSECORE_CCI_IDE_SSH_HOST`。迁移到另一台 Mac 时须在那里配置 SSH 密钥与别名，核验并保存主机指纹。例如验证 Maoshanwang 连接：

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$HOME/.ssh/known_hosts" maoshanwangtech true
```

跳板只负责 TCP 转发；编辑器安装命令通过 IAM 终端在所选 CCI 内执行，不会把跳板当成 CCI。当前支持 Linux x86_64 且存在 Python 3 的容器；首次需要从 GitHub 下载固定的 code-server 4.139.1（约 222 MB），后端校验固定 SHA-256。下载编辑器或 Codex 插件时显示实时字节进度，有总大小时显示百分比，否则显示已下载量；校验、解压和安装阶段显示对应状态。复用已有安装时不显示下载进度。安装、配置、日志、扩展及用户数据位于容器用户的 `~/.local/share/ilab-cci-editor/`，默认打开 `/workspace`，没有该目录时打开用户 HOME。已有 AFS 可通过编辑器「打开文件夹」进入；不改挂载、训练启动指令或系统 SSH。

编辑器绑定该实例内网 IP 的随机端口并启用随机密码。本机后台通过 SSH 转发代办登录，再以独立 localhost 端口代理 HTTP/WebSocket；浏览器只获得 60 秒有效的一次性入口和独立 HttpOnly 会话，不获得 IAM 或编辑器密码。重复进入复用容器内进程。窗口缩放不再重载编辑器；页面回到前台、网络恢复及可见时的周期检查会尝试修复 SSH 转发和认证，保持原代理端口与 iframe，保留未保存内容。「重新连接」也优先修复现有连接，不自动刷新编辑器或重新安装；连接修复成功后，若 VS Code 仍提示断线，可在编辑器内重连。每站最多 4 个本机代理，页面离开后无活跃 WebSocket 且闲置 15 分钟自动回收本机连接。后台重启会断开编辑器页面，重新进入即可。容器内编辑器继续运行以供复用，随容器销毁而消失。

若准备失败，请检查 GitHub 下载、Python 3、本机 SSH 别名/密钥/已核验的 `known_hosts`，以及容器专用目录下的 `editor.log`。此功能会占用容器 CPU/内存与磁盘，具体取决于扩展及工作区大小；关闭页面不停止训练，也不自动结束编辑器内手动启动的进程。需要卸载时先确认并停止这个专用编辑器进程，再移除其专用目录；其他文件和训练任务无需改动。

打开 CCI 编辑器时会检测容器当前用户的 `CODEX_HOME`（未设置时为 `~/.codex`）。发现现有 `config.toml` 或 `auth.json` 后，会自动安装缺失的 Codex 插件并直接使用原目录；不复制、覆盖或返回认证内容，也不替换已安装的有效插件。首次插件安装使用固定 Open VSX Linux x64 版本并校验 SHA-256。插件下载失败时编辑器仍可使用，状态栏会提示，保留未保存内容后重新打开编辑器页面可重试安装。此自动接入针对 Codex 配置，不会把 `~/.codex` 当作通用 VS Code 设置目录。

回归检查：`.venv/bin/python -m pytest tests/test_cci_ide.py tests/test_cci_terminal.py -q` 与 `.venv/bin/python scripts/cci_ide_smoke.py`。后者完全模拟 API，不连接云端或安装编辑器。

2026-10-02 已在 Maoshanwang 本人运行 CCI 上验证浏览器编辑保存（独立读回核对）、内置终端命令执行及免登录重连，并清理临时文件。本机部署后已复验工作区加载。SenseCore 当时无本人运行 CCI，已完成模拟测试但未作云端实测。code-server 自带的可选 VSDA 资源探测与 GitHub 认证提供器仍可能在控制台报错，未阻断上述功能；第三方扩展账号不属于云平台/编辑器自动认证范围。
