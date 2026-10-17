# 跨站数据传输使用说明

入口是“存储管理 → 数据传输”。页面按源端和目标端组织，不再让用户手动选择 upload、download 或 internal / external。

每个端点可以选择“节点文件夹”或“AFS / AOSS”。节点文件夹中的 SenseCore CCI、maoshanwang CCI 来自“运行任务”里当前仍在运行且有可用 Worker / 容器的任务，选择项会显示任务名、实例和容器；SSH node 只展示 SSH 检测成功的主机。选择后用“浏览目录”读取远端路径；SSH 节点不需要额外填写网络。AFS / AOSS 端点只需要选择站点、bucket 和存储路径。

系统按端点自动选择命令和网络：同站 CCI 与本站 AFS 使用 internal，跨站 AFS 同步使用 external，SSH 节点相关路径使用 external。节点文件夹到节点文件夹会自动经过 AFS 临时中转，页面会把 upload、sync、download 分阶段显示。

“增量同步”对应 `ads-sync`，“全量复制”对应 `ads-copy`，目前只有 AFS / AOSS 到 AFS / AOSS 直接同步时开放复制选项。任务会在所选节点或自动选出的源站 CCI 上执行，页面显示阶段进度、最近日志和取消按钮。

CCI 传输通过该任务已有的云端终端执行，SSH 传输继续使用已验证的 SSH 连接。节点上的 ads-cli 继续从 `$MONITOR_ADS_CLI_SCRIPT` 或 `~/initial/tools/ads-cli.sh` 加载，密钥不会进入浏览器请求、任务快照或网页日志。

节点到节点任务使用 `.monitor-transfer/<job-id>/` 作为 AFS 中转前缀，便于失败后保留现场排查；完成后的中转对象需要按存储策略手动清理。
