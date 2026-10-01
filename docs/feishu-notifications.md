# 飞书群通知（本机后台转发）

本机 `charles.monitor-feishu` LaunchAgent 每约 60 秒读取面板的 `/api/state` 和 `/api/task-history`，通过启用签名校验的飞书群机器人发送通知。不需要公网部署，不会修改云任务。

默认设置：

- 两站点 `maoshanwang`、`sensecore` 分别标记；任务名称包含 `charles`。
- 任务开始运行、成功、失败、已停止时通知，附官方控制台链接和记录时间。
- GPU 利用率低于 10% 持续至少 30 分钟，同一运行任务冷却 6 小时。
- 每天北京时间 09:00 发送运行任务汇总；不启用空卡提醒。
- 首次采集建立静默基线，不推送已有任务和旧历史记录。

## 安装与更新

先创建飞书群自定义机器人并启用签名校验。在下面的本机私有路径创建 `config.json`（权限 0600，父目录 0700），不要把真实凭据放入仓库或 `.env`：

```text
~/Library/Application Support/CharlesMonitorFeishu/config.json
```

配置格式（占位值必须换成该机器人的凭据）：

```json
{
  "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/REPLACE_WITH_BOT_ID",
  "secret": "REPLACE_WITH_SIGNING_SECRET",
  "monitor_url": "http://127.0.0.1:8766",
  "low_gpu_enabled": true,
  "low_gpu_percent": 10,
  "low_gpu_seconds": 1800,
  "cooldown_seconds": 21600,
  "daily_hour": 9
}
```

在项目目录执行：

```bash
/usr/bin/python3 scripts/deploy_feishu_relay.py
```

安装器将转发程序复制到本机内置磁盘，保留私有配置和 SQLite 状态，生成 `~/Library/LaunchAgents/charles.monitor-feishu.plist` 并加载。登录后自启，进程异常退出后自动重启。依赖 macOS 上可用的 `/usr/bin/python3`，仅使用 Python 标准库。面板可以独立更新；修改转发代码后再次运行此安装器。

## 查看、暂停、恢复

查看去重基线、已记录任务、待发送及重试耗尽数量（不显示凭据）：

```bash
/usr/bin/python3 "$HOME/Library/Application Support/CharlesMonitorFeishu/feishu_relay.py" --status
launchctl print "gui/$(id -u)/charles.monitor-feishu"
tail -30 "$HOME/Library/Application Support/CharlesMonitorFeishu/relay.err.log"
```

发送一条明确标注的接入测试：

```bash
/usr/bin/python3 "$HOME/Library/Application Support/CharlesMonitorFeishu/feishu_relay.py" --test
```

暂停，且下次登录不自启：

```bash
launchctl disable "gui/$(id -u)/charles.monitor-feishu"
launchctl bootout "gui/$(id -u)/charles.monitor-feishu"
```

恢复：

```bash
launchctl enable "gui/$(id -u)/charles.monitor-feishu"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/charles.monitor-feishu.plist"
```

修改 `config.json` 后重启已加载的转发服务：

```bash
launchctl kickstart -k "gui/$(id -u)/charles.monitor-feishu"
```

关闭 GPU 提醒：`low_gpu_enabled` 改为 `false`；关闭每日汇总：`daily_hour` 改为 `null`。`--once` 只用于停止后台进程后的诊断，它会进行一次真实采集和发送；互斥锁会拒绝重复运行实例。

## 通知口径与限制

- 当前任务消失不能证明其结束：必须取得新鲜且成功的历史查询结果才能确认。接口失败和超过 5 分钟的源数据不用于状态通知。
- GPU 数据必须数值有效、采集成功且不超过 5 分钟。缺失、失败、采样间隔超过 5 分钟会中断低利用率计时。使用面板已有的任务级 GPU 指标。
- 首次历史查询晚于当前任务查询时，仍可报告已观察到运行的任务结束；未知旧历史不推送。没有可靠结束时间、且从未被转发服务观察到运行的短任务可能漏报（CCI 尤其如此）。
- 待发送消息和去重状态保存在 `relay.sqlite3`。发送失败按入队顺序指数退避，最多 12 次实际尝试，最长保留 24 小时；耗尽后计入 `--status` 的 `dead` 数量。飞书没有该 Webhook 的幂等键，若远端已接收而响应丢失，重试有小概率重复。
- 每日汇总每个日期最多入队一次；第一次在 09:00 后启动不会补发当天汇总。后续停机恢复时，会在当天 09:00 后补发当日汇总，不逐日追补。不可用站点会明确标记，任务很多时省略部分详情以控制消息大小。
- 电脑休眠、关机或断网期间无法持续采集/发送。恢复后会重试有效的待发消息；仅登录自启不会阻止系统休眠。
- 本地 `127.0.0.1:8766` 不能在手机上访问。通知使用官方控制台 HTTPS 链接，手机可能需要登录相应站点。

## 验证

```bash
python -m pytest tests/test_feishu_relay.py -q
```

覆盖签名、飞书应用错误码、首次静默、两站点隔离、重启去重、源数据过期、任务消失、低利用率连续性与冷却、发送重试和每日汇总大小限制。
