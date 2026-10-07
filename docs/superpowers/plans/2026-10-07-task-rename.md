# 云任务重命名实施计划

**Goal:** 在任务资源监控标题右侧增加编辑按钮，按用户确认同步修改云端显示名称。

**Architecture:** 独立 TaskRenamer 校验本站本人 CCI/ACP 任务，通过固定云端 PATCH 更新 display_name，再读回核实。复用操作记录防止同一请求重复写入。独立前端弹窗负责保存、取消、错误和结果核对；确认结果通知主页面按 ID 更新名称。

**Tech Stack:** FastAPI、httpx、SQLite 操作记录、原生 JavaScript/CSS、pytest、Playwright。

## 交互与约束

- 名称右侧铅笔按钮，打开预填名称的弹窗，支持保存、取消、Enter 与 Escape。正在保存时禁止重复提交及站点切换。
- 云平台显示名规则：1–256 字符，以中英文或数字开头，后续允许中英文、数字、下划线、连字符、点、冒号；沿用本站名称包含 charles 的监控范围。
- 不更改任务 ID、资源、命令或运行状态；只允许当前工作空间本人任务。
- 请求带原名称，检测过时编辑；保存只写一次，持久化 request_id。网络不明确时保留同一请求供只读核对；check_only 模式在无操作记录时返回 not_submitted，不产生云请求。
- 仅云端读回新名称后报告成功；旧采样快照不立即覆盖已核实的新名称，之后以新采样为准。
- 移动端沿用设备写权限，深浅色主题及窄屏布局沿用现有设计。
- 验证使用模拟云端，不重命名真实任务。

## 已核实协议（2026-10-07）

两个站点的控制台微前端版本一致：CCI umi.026c9a4f.js、ACP umi.8c1c5e81.js。CCI 368.e5c09b90.async.js 的 updateDisplayName 调用 PATCH /compute/cci/data/v2{workspace}/apps/{name}，body 仅 display_name，携带 x-ui-valid；该版本底层没有发送 update_mask。ACP 771.470523f7.async.js 的 nQ/k 使用 PATCH /compute/acp/data/v2{workspace}/trainingJobs/{name}，详情组件传入 body 仅 display_name。

## 步骤

- [x] 后端：先以 MockTransport 验证两种任务、两个站点的精确请求与读回；覆盖归属/名称校验、冲突、超时重放、鉴权与取消。实现 server/task_rename.py。
- [x] 路由：POST /api/tasks/rename，严格字段和本地请求保护；接入 SiteRuntime 与移动网关写白名单。验证站点隔离和只读设备拒绝。
- [x] 页面：增加标题按钮、弹窗与独立 task-rename.js/css；主脚本接收已核实的名称事件，保持任务 ID 和曲线；sites.js 在保存中锁定站点切换。
- [x] 浏览器：先运行失败的重命名验收，覆盖 CCI/ACP、取消、成功、校验、冲突、迟到响应、重复点击、核对结果、刷新恢复和窄屏/主题。
- [x] 回归与独立代码审查，修复实际发现的问题；更新本机运行副本、重启并只读检查入口可用。
