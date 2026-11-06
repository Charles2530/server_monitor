# SenseCore 首次启动可靠性实施计划

> **For agentic workers:** Execute inline with the executing-plans workflow. Verify each task before advancing and request an independent review of the resulting diff.

**Goal:** 新容器遇到下载波动或控制连接中断后自动核对和有限重试，正确显示后台进度，保留已有编辑器进程与浏览器内容。

**Architecture:** 保留现有容器安装锁和哈希校验，增加独立的只读状态脚本。后台 worker 先核对实际安装状态，再决定等待现有安装或启动一次安装；通过本地缓存向页面报告状态。恢复流程触发有限重试，details 仍为本地只读接口。

**Tech Stack:** Python 3.9+、asyncio、urllib、fcntl、FastAPI、原生 JavaScript、pytest、Node test runner。

## 1. Codex 下载的有限重试

Files: `server/ide_codex.py`, `tests/test_ide_codex.py`.

- [ ] 增加失败测试，第一次连接重置后第二次下载成功；连续失败到达上限；损坏包不执行安装器；自动代理失败可使用请求级直连 opener，显式代理保持生效。
- [ ] 保留 250 MiB、SHA256、读超时和总时限，仅对网络错误重试两次；安装失败写容器专用日志，浏览器不接收原始异常文本。
- [ ] Run `.venv/bin/python -m pytest tests/test_ide_codex.py -q`，全部通过。

## 2. 不干扰编辑器的状态核对

Files: `server/ide_bootstrap.py`, `tests/test_ide_setup_status.py`, `tests/test_ide_progress.py`.

- [ ] 在临时 HOME 中创建安装锁与模拟已安装插件，测试只读脚本在锁占用时立即返回，文件内容与时间戳不变，不调用下载、安装或进程启动。
- [ ] 状态脚本验证容器 hostname、操作系统、架构和目录所有者，返回 codex/appearance/extensions/asset_roots/install_running 及白名单进度字段，不返回密码、配置正文或登录材料。
- [ ] 快速 bootstrap 已有健康编辑器时不等待后台安装锁；完整安装仍使用独占锁。后台报告进度到原子替换的容器专用 JSON。
- [ ] 去除嵌套压缩，模块源码缓存后由已有控制传输统一压缩，保持已有 fast-bootstrap 20 KB 测试上限。
- [ ] Run `.venv/bin/python -m pytest tests/test_ide_setup_status.py tests/test_ide_progress.py tests/test_ide_latency.py -q`，全部通过。

## 3. 后台核对与有限重试

Files: `server/cci_ide.py`, `tests/test_ide_setup_status.py`, `tests/test_ide_latency.py`.

- [ ] 测试控制连接丢失、远端仍在安装、随后全部安装成功的过程，验证只读核对同步标志与资源根目录，编辑器端口、PID 和浏览器认证不变。
- [ ] worker 首先只读核对，锁占用时等待，不重复安装。锁释放但缺插件时有限重试；连续核对失败停止，不无限创建终端。
- [ ] 安装与核对共用 30 分钟总预算，每个会话最多三次安装；正常 recover 可恢复未完成的后台任务，显式 reopen 可在冷却后重新尝试。关闭会话取消整个恢复链。
- [ ] details/open 返回白名单 optional_state/optional_progress/optional_attempts，不暴露异常、下载地址或凭据。
- [ ] Run `.venv/bin/python -m pytest tests/test_ide_setup_status.py tests/test_ide_latency.py tests/test_ide_recovery.py -q`，全部通过。

## 4. 页面状态与故障回归

Files: `static/ide.js`, `tests/ide_frame_connection.test.cjs`.

- [ ] 显示安装、核对、退避重试和最终失败；pending 贯穿恢复链。使用安全阶段名与字节数，不显示服务端原始错误。
- [ ] 测试 pending 期间轮询，完成后停止，状态变化不替换或重载已有 iframe。
- [ ] Run `node --test tests/ide_frame_connection.test.cjs` 和 `node --check static/ide.js`，全部通过。

## 5. 验证、审查和运行同步

- [ ] 增加完整首次安装成功及二次打开无下载测试；不删除当前容器的插件或设置来制造“首次”。
- [ ] 执行所有 IDE 相关 Python 与 Node 测试、`git diff --check`；修复本次造成的问题。
- [ ] 请独立审查代理复查最终改动，处理实质问题。
- [ ] 核对 macOS 自动同步后的文件哈希，使用已有容器做非破坏性连接和 Codex 面板检查。
- [ ] 交付实际覆盖的场景与限制，不承诺外部网络永远可用，不自动创建新的付费云资源。
