# 新增功能页面美化

**目标：** 将连接日志、编辑器准备状态、GPU 运行队列和移动端任务入口统一到现有浅色／深绿设计。

**实现：** 在现有组件 CSS 中调整视觉层次，复用 `visual-tokens.css` 的配色；保留 DOM、接口、连接恢复、排序和筛选行为。此次为可逆的展示层调整，使用已有浏览器验收及截图检查，不新增镜像样式实现的单元测试。

## 实施清单

- [x] `static/connection-log.css`：日志弹窗分区、状态徽标、事件行、可滚动列表和小屏布局；保留全屏与焦点模式规则。
- [x] `static/session-appearance.css`：准备进度和后台状态提示使用统一强调色，区分需要关注的状态；避免挤占已打开的编辑器。
- [x] `static/utilization-queue.css`：筛选控件、排名、利用率、辅助信息和手机卡片统一配色与间距。
- [x] `static/mobile-task-workspace.css`：终端入口与分段工具栏层次、触摸反馈、提示卡片。
- [x] 验收：`connection_log_smoke.py`、`session_theme_smoke.py`、`mobile_task_workspace_smoke.py`、`ide_experience_smoke.py`、`waiting_links_smoke.py`；运行前端行为检查并目视检查浅深色桌面／手机截图。

## 验收边界

至少覆盖 320、390、1440 像素；连接日志还需覆盖 320 × 400 的低高度场景。确认关闭按钮可见、长日志与任务名不横向溢出、键盘焦点可见，空列表与未知利用率仍能区分。测试使用本地模拟数据。

## 验证结果

- 五组现有浏览器验收全部通过：连接日志、会话主题、移动任务工作区、编辑器体验、队列链接。
- `node --test scripts/frontend_efficiency_check.js`：47 passed。
- 本地模拟数据截图验收：浅深色、320 / 390 / 768 / 1440 宽度；运行队列排序和筛选、0% 与未知值、空列表、编辑器准备进度、日志弹窗在 320 × 400 下的关闭按钮和滚动区域均通过。
- 修正主题样式优先级，确认未知利用率在浅深色下均使用弱化文字色。
- 截图与临时验收脚本保存于被 Git 忽略的 `data/new-feature-polish/`，未连接或修改云端任务。
