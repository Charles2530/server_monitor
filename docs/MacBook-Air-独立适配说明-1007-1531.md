# MacBook Air 独立适配

记录日期：2026-10-07。使用同一 `main`，机器参数独立维护。Air 隔离代码提交为 `e55b8af`。

## 本机入口

在仓库根目录执行：

```sh
./deploy-air.sh
```

此入口固定使用 `config/runtime-profiles/macbook-air-current.json`，不接受覆盖参数。已有部署的常规更新也会保留匹配硬件、`selection=manual` 的配置。

标准部署会安装锁定依赖、复制仓库代码与仓库 `.env`，停止并重新加载本机服务；执行前应按既有流程备份实际部署并确认仓库 `.env` 适用于本机。本次同步单独保留了实机 `.env`、数据库、会话、依赖和完整 plist，未运行覆盖环境的标准安装流程。

## 适用范围

| 项目 | Air 独立配置 |
|---|---|
| 型号 / 架构 | Mac16,12 / arm64 |
| CPU / 内存 | 10 核（4P+6E）/ 16 GiB |
| launchd ProcessType | Standard |
| 采集 / 指标 / 队列并发 | 6 / 4 / 3 |
| 终端会话上限 / 目标缓存 | 8 / 8 秒 |
| 空闲 / 忙碌 / 错误轮询 | 10 / 10 / 10 秒 |

Air 专属值只保存在独立 JSON 中；通用自动检测不再包含 Air 调优特例。删除配置或显式选择 `auto` 会恢复通用预设，不等于恢复最早实机部署。

部署器拒绝把带硬件信息的 Air 配置显式应用到型号、架构、核数或内存不同的机器，并在安装、复制和停止服务前完成校验。运行时遇到不匹配配置会回退本机自动预设。绑定的是硬件规格，不是设备序列号；没有 `hardware` 的历史通用模板仍兼容。

Mini 文件和原有自动策略均未修改。以后在 Mini 实测、备份后更新其独立 JSON 和部署入口，无需维护另一套功能分支。

## 实机状态与回退

本机生效文件为 `~/Library/Application Support/CharlesCloudMonitor/runtime-profile.json`，与 Air JSON 字节一致；SHA256：`91e233b4dd06ebed94c1bc35ad1a6b4dfb52537de217e81f2e4db4f2ff5e500b`。系统节能和温控设置保持原样，电池低电量模式开启。

本轮同步前备份：`/Users/charles/Library/Application Support/CharlesCloudMonitor-backups/function-audit-20261007-133807`。包含完整代码/config 归档、完整 launchd plist、SQLite 一致性备份、会话备份和 SHA256 清单。凭据未进入验收附件。

需要回退本轮功能同步时执行：

```sh
python3 "/Users/charles/Library/Application Support/CharlesCloudMonitor-backups/function-audit-20261007-133807/rollback.py"
```

回退会先校验当前文件是否仍为本轮部署版本，再停止旧进程、恢复本轮前代码并重新加载；保留当前数据库、云会话及 Air 参数。若后续修改了文件，脚本会拒绝覆盖，须先核对差异。恢复最初 Background 调度和未调优代码，使用此前调优报告中的原始备份流程。

原始测量见 [Air 调优结果](MacBook-Air-运行调优结果-1007-1327.md)，本轮验收见 [MacBook-Air-功能验收报告-1007-1531.md](MacBook-Air-功能验收报告-1007-1531.md)。
