# 跨站数据传输使用说明

页面入口：`存储管理 → 数据传输`。

先选择执行节点类型和节点。SenseCore / maoshanwang 节点需要在节点上安装 `initial/tools/ads-cli.sh`；SSH node 使用当前 `~/.ssh/config` 中的 external SSH 连接。

上传时填写执行节点上的绝对路径和 AFS/AOSS 目标路径；下载时填写 AFS/AOSS 源路径和执行节点上的目标目录。目录路径保留尾部 `/`。跨站同步选择“AFS / AOSS → AFS / AOSS”，分别填写源站点、源网络、源路径和目标站点、目标网络、目标路径。

“增量同步”使用 `ads-sync`，适合重复同步权重目录；“全量复制”使用 `ads-copy`，适合一次性完整复制。任务会在选中的远端执行节点上运行，网页显示阶段进度、最近日志和取消按钮。

真实执行前请确认执行节点能同时访问源端和目标端。SSH 端点固定走 external；SenseCore 和 maoshanwang 的 AFS/AOSS 网络入口由表单分别选择。页面不会展示或上传 AK/SK。
