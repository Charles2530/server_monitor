# macOS 无人登录启动

默认部署使用用户 LaunchAgent，登录后启动。需要停留在登录界面时也运行监控，可将已部署服务迁移到系统 LaunchDaemon。业务进程继续使用指定普通用户身份，不以 root 采集或读取云凭据。

系统服务覆盖主监控、手机网关、独立 Tailscale 网络进程，以及已安装的飞书通知和看板服务。不会修改云任务、自动登录、FileVault 或电源配置。FileVault 开启时，冷启动仍需要先解锁磁盘；本部署不绕过磁盘加密。网络必须在登录前可用：用户登录后才启动的代理不属于这套系统服务。

## 先准备运行副本

按 README 和手机交接文档部署主监控、网关及需要的飞书服务。代码、Python 环境、会话和数据库必须位于内置磁盘的 Application Support，不能依赖登录后才挂载的外置卷。迁移保留当前数据和手机权限。

macOS 的 Tailscale 图形客户端与开源 `tailscaled` 是不同的运行方式。本方案使用开源进程的 userspace networking，仅为本机网关提供 tailnet Serve，不创建系统 VPN 接口、不接管系统 DNS，不提供出口节点或子网路由。首次仍需使用同一 tailnet 的账号授权。

已验证的构建版本为 Go 1.27.1、Tailscale v1.102.4。安装已核验来源的 Go 后，以普通用户构建：

```bash
mkdir -p "$HOME/Library/Application Support/CharlesMonitorNetwork/bin"
chmod 700 "$HOME/Library/Application Support/CharlesMonitorNetwork"
GOBIN="$HOME/Library/Application Support/CharlesMonitorNetwork/bin" \
  go install -ldflags '-X tailscale.com/version.longStamp=1.102.4-local-monitor -X tailscale.com/version.shortStamp=1.102.4' \
  tailscale.com/cmd/tailscale@v1.102.4 tailscale.com/cmd/tailscaled@v1.102.4
```

二进制、状态、证书、配对信息均留在 Git 外。可先以前台进程或临时用户 LaunchAgent 验证网络，执行参数与 `scripts.boot_services.network_payload()` 一致：

```bash
"$HOME/Library/Application Support/CharlesMonitorNetwork/bin/tailscaled" \
  --tun=userspace-networking \
  --statedir="$HOME/Library/Application Support/CharlesMonitorNetwork" \
  --state="$HOME/Library/Application Support/CharlesMonitorNetwork/tailscaled.state" \
  --socket="$HOME/Library/Application Support/CharlesMonitorNetwork/tailscaled.sock" \
  --port=0
```

在另一个终端登录并配置私有 Serve：

```bash
NETWORK="$HOME/Library/Application Support/CharlesMonitorNetwork"
"$NETWORK/bin/tailscale" --socket="$NETWORK/tailscaled.sock" up \
  --accept-dns=false --hostname=charles-monitor-boot
"$NETWORK/bin/tailscale" --socket="$NETWORK/tailscaled.sock" serve status
"$NETWORK/bin/tailscale" --socket="$NETWORK/tailscaled.sock" serve --bg --https=443 http://127.0.0.1:8767
```

先检查已有 Serve，不覆盖其他业务，也不运行 Funnel。GUI 客户端和此进程有独立网络身份；切换前核对同一个 tailnet、新 Serve、证书及手机访问。若要沿用旧网关域名，应先将旧节点改名，再给新节点设置旧名字，并核对实际 DNSName；不能只假定域名没有变化。无法保留域名时需要给手机生成新配对码。保留旧客户端配置，验证后再关闭其登录自启动。改名后 Serve 可能仍保留旧域名映射，应核对 `serve status --json` 并只保留本监控使用的入口。

如果图形客户端 DNS 与其他 VPN/代理冲突导致 ACME 解析超时，先用 `tailscale set --accept-dns=false` 验证系统 DNS；无界面节点保持 `--accept-dns=false`。不要关闭证书验证，也不要依赖一次签发成功替代后续续期检查。`--statedir` 必须显式指定，以供证书持久化。

## 安装系统服务

先查看将迁移的服务和执行文件（用户名替换为实际普通用户）：

```bash
/usr/bin/python3 scripts/boot_services.py plan --user charles
sudo /usr/bin/python3 scripts/boot_services.py install --user charles
```

管理员密码仅在 Mac 本机输入，不写入脚本或配置。安装器预先备份原有 plist，停止原用户实例，将用户自动启动入口移到备份目录，再将系统 plist 写入 `/Library/LaunchDaemons/`。使用 `RunAtLoad`、`KeepAlive` 和普通用户的 `HOME`；用户退出登录不会停止这些系统作业。

主监控、网关及飞书服务都应先部署过；只有前两项强制存在，飞书服务按本机已安装情况迁移。网络进程若使用临时 LaunchAgent，应使用 `charles.monitor-network` 标签，使安装器能停止旧实例并备份入口。不要留下另一个前台 tailscaled 进程占用同一状态和 socket。

备份和迁移记录位于：

```text
~/Library/Application Support/CharlesMonitorBoot/
```

安装器检查实际运行 PID、连续稳定运行和本地 HTTP 就绪；失败会尝试恢复原 LaunchAgent，保留错误以供排查。重复安装已存在的系统服务会被拒绝，避免覆盖无法确认归属的配置。

## 验证、管理和更新

```bash
./status.sh
/usr/bin/python3 scripts/boot_services.py status --user charles
.venv/bin/python scripts/mobile_host.py status
curl --fail http://127.0.0.1:8766/api/state
curl --fail http://127.0.0.1:8767/health
```

应看到五项或已选择的系统服务被加载，并确认 `launchctl print system/服务标签` 有实际运行 PID。HTTP 检查必须同时核对快照时间，不能只把旧数据或已加载 plist 当作采集恢复。手机网关仍只绑定回环 8767，主监控仍只绑定回环 8766。

系统服务重启需要管理员权限，例如：

```bash
sudo launchctl kickstart -k system/charles.server-monitor
sudo launchctl kickstart -k system/charles.server-monitor-mobile
sudo launchctl kickstart -k system/charles.monitor-network
```

已安装系统模式时，原用户安装器会拒绝创建重复服务。更新代码可先回退用户模式、运行相应部署脚本，然后重新安装系统模式：

```bash
sudo /usr/bin/python3 scripts/boot_services.py rollback --user charles
.venv/bin/python scripts/deploy_launchagent.py
.venv/bin/python scripts/mobile_host.py install
# 只有更新飞书代码时才运行相应的飞书部署脚本。
sudo /usr/bin/python3 scripts/boot_services.py install --user charles
```

回退会恢复原用户 plist 和迁移时运行中的实例；无人登录时恢复文件并等待下次登录启动。保留网络状态、监控数据库与配对权限。网络 GUI 的启停和名称切换另行恢复，不由此脚本猜测。

最终无人值守验收应真实重启 Mac、停留在登录界面，再用另一网络的手机检查 HTTPS、两个站点的新快照以及日志。未实际重启时，只能报告 system 域服务和配置验证通过。飞书发送/表格同步沿用用户原来的配置，本机不会通过创建或停止真实云任务测试重启恢复。
