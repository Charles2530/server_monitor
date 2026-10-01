# 飞书监控看板

将本机监控数据维护在飞书多维表格中，每个任务一行，可从手机飞书查看。同步程序是纯 Python；直接只读打开两站点的本地 SQLite 数据库，不调用大模型，不依赖面板 HTTP API，也不公开本机端口。

## 当前接入状态

2026-10-01：已创建「服务器监控看板」，专用应用「服务器监控表同步」1.0.0 已发布，仅对应用所有者可用。已开通记录新增/搜索/更新及字段新增/读取/更新六项权限，并通过「添加文档应用」授予本看板的可编辑权限。

接入已完成，按用户要求拆为四张独立数据表：

| 数据表 | 验收记录数 |
| --- | --- |
| 茅山王任务 | 6 个任务 |
| 茅山王 GPU 卡数量 | 4 个集群 |
| SenseCore 任务 | 3 个任务 |
| SenseCore GPU 卡数量 | 7 个集群 |

已验证四张表的二次同步只更新原行，没有重复同步标识或跨站点记录。当前 11 个集群均有可用卡数，其中 9 个有总量，另外 2 个明确显示「总量不可用」。拆分时先确认 SenseCore 新表完整包含原记录，再从茅山王表移除其旧副本；私有目录保留迁移前的记录备份。

LaunchAgent 已运行，部署文件与源码一致，私有配置权限为 0600。当前配置为每轮完成后间隔 60 秒，四表约每分钟更新。看板已加入云文档置顶，入口及说明已发送到「服务器监控通知」群。已在桌面验证表格显示；手机端需使用有权限的同一账号打开，尚未代替用户在手机端验收。当前全套测试 257 项通过。

## 表格内容

任务名称、站点、状态、GPU 数量、GPU 利用率 (%)、显存占用 (%)、数据状态、采集时间、同步时间、集群、类型、官方任务链接、同步标识。

- 当前部署约每分钟同步，同一站点/任务更新原来的行；未配置 interval_seconds 时程序默认 300 秒。
- GPU 与显存均沿用本机面板的任务级百分比口径，0 是有效值。
- 源或指标超过 5 分钟、失败或无效时，不把旧指标伪装为当前值：指标留空，明确标记异常。
- 任务消失时保留记录，显示「待确认 / 未在运行列表」，不推断已经成功、失败或停止。本版仅同步运行快照，不查询历史结束状态。
- 采集时间与同步时间分开；电脑关机或断网后，云端表格保留最后一次数据。查看时要留意时间，不保证云端自动标记已离线。
- 不同步全部用户的等待队列，不同步密码、会话凭据、终端或日志内容。

## GPU 卡数量

两个站点各有独立的 GPU 卡数量表，按集群展示可用卡数、总卡数、异常节点卡数、节点数、异常节点数和数据状态。公共资源池不参与统计。四张表的同步标识列均在默认视图中隐藏，模板自带的空白行通过非空筛选隐藏。

总卡数是节点设备总量，包含异常节点；可用卡数沿用平台的可调度余量，不能直接用两者差值推断已用量。余量和总量分别显示采集时间，独立验证新鲜度；任一来源失败、超过 5 分钟或值无效时，相应数字留空。缺少总量时仍展示有效余量；集群消失时保留记录并清空数量。数据状态表示采集是否完整，节点是否异常由异常节点数和异常节点卡数表示。

## 飞书授权

由当前账号在飞书开发者后台创建专用自建应用。配置多维表格记录读取、新增、更新，以及初始化所需字段读取/新增/更新权限；也可使用飞书提供的对应多维表格管理权限。应用文档访问只授权本看板，勿将其他私人文档加入。

通过表格菜单「更多 → 添加文档应用」将应用加入该表。审批/发布要求以当前飞书租户页面为准。用于程序的 App ID 和 App Secret 与群机器人的 Webhook 是不同凭据，不能互换。

所有真实凭据写到本机私有文件，权限 0600，父目录 0700：

```text
~/Library/Application Support/CharlesMonitorBitable/config.json
```

格式：

```json
{
  "app_id": "REPLACE_WITH_APP_ID",
  "app_secret": "REPLACE_WITH_APP_SECRET",
  "app_token": "REPLACE_WITH_BASE_TOKEN",
  "site_tables": {
    "maoshanwang": {
      "tasks": "REPLACE_WITH_MAOSHANWANG_TASK_TABLE_ID",
      "resources": "REPLACE_WITH_MAOSHANWANG_RESOURCE_TABLE_ID"
    },
    "sensecore": {
      "tasks": "REPLACE_WITH_SENSECORE_TASK_TABLE_ID",
      "resources": "REPLACE_WITH_SENSECORE_RESOURCE_TABLE_ID"
    }
  },
  "interval_seconds": 60
}
```

app_token 和四个数据表 ID 来自目标表格 URL。site_tables 必须同时配置两个已知站点，四个目标表 ID 不能相同。每个同步作业只接收其站点的快照，源缺失仍会标记该站点数据不可用。程序仍兼容旧的 table_id / resource_table_id 配置，但当前部署使用四表配置。不要把凭据提交到 Git 或填进监控网站已有的 `.env`。

## 本地验证与部署

无需飞书凭据即可验证直接读取本机数据（只输出条数、站点、健康状态）：

```bash
/usr/bin/python3 scripts/feishu_bitable.py --dry-run
python -m pytest tests/test_feishu_bitable.py tests/test_feishu_resources.py tests/test_feishu_site_tables.py -q
```

凭据和表格授权完成后，初始化字段并进行一次真实同步：

```bash
/usr/bin/python3 scripts/feishu_bitable.py --init
```

部署或更新后台服务：

```bash
/usr/bin/python3 scripts/deploy_feishu_bitable.py
```

程序和状态位于 `~/Library/Application Support/CharlesMonitorBitable/`，LaunchAgent 标签为 `charles.monitor-bitable`。初始化或首次同步失败时不加载自动启动。

查看状态：

```bash
launchctl print "gui/$(id -u)/charles.monitor-bitable"
cat "$HOME/Library/Application Support/CharlesMonitorBitable/maoshanwang.tasks.status.json"
cat "$HOME/Library/Application Support/CharlesMonitorBitable/maoshanwang.resources.status.json"
cat "$HOME/Library/Application Support/CharlesMonitorBitable/sensecore.tasks.status.json"
cat "$HOME/Library/Application Support/CharlesMonitorBitable/sensecore.resources.status.json"
tail -30 "$HOME/Library/Application Support/CharlesMonitorBitable/sync.err.log"
```

暂停（包含下次登录）：

```bash
launchctl disable "gui/$(id -u)/charles.monitor-bitable"
launchctl bootout "gui/$(id -u)/charles.monitor-bitable"
```

恢复：

```bash
launchctl enable "gui/$(id -u)/charles.monitor-bitable"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/charles.monitor-bitable.plist"
```

配置修改后：

```bash
launchctl kickstart -k "gui/$(id -u)/charles.monitor-bitable"
```

同步锁会阻止重复实例。失败留到下一周期重试；每次写入前重新读取远端记录。新增请求的 UUIDv4 和原始内容持久化到私有 SQLite，响应丢失后复用幂等标识；发现重复同步标识时停止写入，避免错误匹配。程序不删除表格记录。

四张表使用独立同步状态和新增请求记录；后台循环中一张表请求失败不会阻止其他表同步。每轮的网络请求耗时会计入实际刷新延迟，因此不是秒级推送。旧配置遗留的 status.json/resources.status.json 不再更新；请查看上述四个状态文件。拆分前已验证旧 pending journal 为空，新表无需继承未决新增请求。

## 验收

部署前须验证：首次同步出现正确任务；二次同步只更新不增行；表格在桌面飞书可见；手机以同一账号可打开；关闭本机程序后仍能看到最后记录及时间。应用密钥和底层会话不会上传到表格。
