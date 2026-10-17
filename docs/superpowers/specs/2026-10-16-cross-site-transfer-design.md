# 跨站数据传输设计

## 目标

在现有“存储管理”页面增加统一的数据传输工作区，使用用户选择的 SenseCore CCI、maoshanwang CCI 或 SSH node 作为执行节点，在执行节点上完成 AFS/AOSS、CCI 文件夹和 SSH 文件夹之间的文件或目录传输。

第一版保留 SSH 管理中的现有 SCP 上传/下载逻辑，并增加快捷入口；新页面负责跨端任务。SSH 端点固定使用 external，SenseCore 与 maoshanwang 的 AOSS 端点可以选择 internal 或 external。

## 页面与交互

“存储管理”增加两个标签：

- AFS 存储：保留现有只读容量和挂载关系页面。
- 数据传输：新增跨端传输工作区。

数据传输工作区按顺序包含：

1. 执行节点：类型为 SenseCore、maoshanwang 或 SSH node，选择后展示后端返回的节点列表和能力状态。
2. 源端：CCI 文件夹、AFS/AOSS 路径或 SSH 文件夹。
3. 目标端：CCI 文件夹、AFS/AOSS 路径或 SSH 文件夹。
4. 传输策略：增量同步 `sync` 默认，全量复制 `copy` 可选；目录路径保留尾部 `/`，文件路径不添加尾部 `/`。
5. 任务状态：排队、执行、完成、失败、取消，显示阶段、当前文件、已传大小、速度、最近日志和取消操作。

端点选择不依赖顶部当前站点切换器。每个 AFS/AOSS 端点显式选择站点、网络、bucket 和路径；SSH 端点从当前 SSH catalog 选择主机，网络固定显示 external。点击 SSH 管理卡片上的跨端入口时，页面预填主机和路径。

## 后端边界

新增独立的传输任务模块，不修改 `SSHTransfers` 的单机 SCP 生命周期。传输任务由以下职责组成：

- `TransferManager`：校验任务、限制并发、保存任务状态、启动和取消后台任务。
- `ExecutorCatalog`：返回 SenseCore / maoshanwang CCI 和 SSH node 列表及能力。
- `EndpointResolver`：校验端点结构、路径类型和网络设置。
- `AdsTransferWorker`：在选定 CCI 上执行 `ads-upload`、`ads-download`、`ads-sync` 或 `ads-copy`。
- `SSHTransferWorker`：复用 SSH catalog 和安全命令参数，处理 SSH 端点。

建议接口：

```text
GET  /api/transfers/executors
POST /api/transfers/browse
POST /api/transfers
GET  /api/transfers
POST /api/transfers/{id}/cancel
```

接口只接受结构化 JSON，不接受任意 shell 命令。AK/SK 不进入浏览器请求体、任务快照或日志；站点密钥继续由执行节点的 ads-cli 配置提供。

## 传输路由

- CCI 文件夹 → AFS/AOSS：选定 CCI 执行 `ads-upload`。
- AFS/AOSS → CCI 文件夹：选定 CCI 执行 `ads-download`。
- AFS/AOSS → AFS/AOSS：选定 CCI 执行 `ads-sync`，必要时执行 `ads-copy`。
- SSH 文件夹相关任务：选定执行节点必须同时具备到 SSH external 和 AFS/AOSS 的网络与认证能力；不能满足时，在创建任务前失败并返回能力原因。
- SSH → SSH：通过执行节点完成远端传输或临时 staging，任务状态中明确显示 staging 阶段。

执行前进行能力预检查：节点在线、ads-cli 可用、源路径可读、目标路径可写、网络入口可达、SSH 认证可用、临时空间满足要求。预检查失败不会创建后台任务。

## 安全与错误处理

- 所有路径由后端按端点类型分别校验；SSH 路径要求绝对路径，AOSS 路径按 bucket prefix 处理。
- 只允许普通文件和目录，禁止根路径和任意命令参数。
- 任务日志只保留经过过滤的阶段和错误摘要，过滤 URL 中的密钥和签名信息。
- 传输失败保留可读错误、执行节点、源端、目标端和阶段；不暴露原始认证诊断。
- 取消操作终止执行节点上的子进程，并清理 staging 目录。

## 验证

后端测试覆盖端点校验、执行节点列表、路由选择、sync/copy 命令参数、预检查失败和取消清理。前端测试覆盖存储页标签、表单校验、站点/网络选择、任务轮询、错误展示和移动端布局。真实 CCI 验证以已确认的目录同步路径为准：SenseCore/maoshanwang AFS 互传、CCI 文件夹与 AFS 互传；SSH 相关组合在执行节点能力检测通过后再执行。
