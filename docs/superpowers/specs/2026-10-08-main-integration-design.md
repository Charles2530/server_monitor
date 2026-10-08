# Main Branch Integration and Adaptive Mac Runtime Design

**Date:** 2026-10-08

## Goal

将现有功能统一收敛到 `main`，让同一套代码自动适配已实测的 MacBook Air 和 Mac mini，降低终端实现之间的耦合，清理 GitHub 中的测试源码，并把后续部署入口固定为 `main`。

## Current context

`main` 已经包含 Feishu、移动端、无人值守启动、Mac runtime 和界面优化等历史分支的提交。当前 `codex/full-access-context-scope` 只额外包含 Codex 上下文、IDE 配置和前端轮询效率改进。因此整合工作应以现有 `main` 为基线，通过一个可审查的 PR 带入缺失提交和重构，不重复合并已经存在于 `main` 历史中的旧提交。

## Design

### 1. PR and branch lifecycle

1. 从 `main` 创建 `codex/main-integration`。
2. 将当前 Codex/效率提交带入整合分支。
3. 对所有现有功能分支执行祖先关系和文件级核对；只有 `main` 缺失的功能才合并或摘取。
4. 在整合分支完成自动 profile、终端解析基类、Git 忽略规则和部署入口调整。
5. 本机完成完整 Python、前端和 profile 验证后，创建 PR 到 `main`。
6. PR 合并后确认 `main` 的提交和工作区状态，再删除除 `main` 外的本地与远端分支。

不使用强制推送，不直接覆盖 `main` 的历史，不在未验证的状态下删除分支。

### 2. Hardware-adaptive runtime profiles

`server/runtime_profile.py` 保留通用 CPU/内存 fallback，同时增加已测机型的精确匹配：

| Hardware signature | Profile behavior |
| --- | --- |
| `Mac16,12`, `arm64`, 10 cores, 16 GiB | `m4-balanced`, collector 6, metric 4, queue 3, terminal 8, cache 8s, polling 10/10/10, `Standard` |
| `Mac16,10`, `arm64`, 10 cores, 24 GiB | `m4-balanced`, collector 6, metric 4, queue 6, terminal 8, cache 8s, polling 30/10/15, `Interactive` |
| Other hardware | Existing conservative generic tiers |

`scripts/deploy_launchagent.py` 默认使用硬件检测结果。显式传入 profile 文件时仍执行完整 schema 和硬件匹配校验；不匹配的文件在停止服务或复制代码之前拒绝。已有匹配硬件的手动 profile 继续兼容，以便安全回滚，但普通部署不需要 Air/Mini 专用分支或专用代码路径。

### 3. Low-coupling terminal resolver hierarchy

新增一个终端解析基类，使用模板方法固定公共流程：

- 构造 site、transport、session loader 和目标缓存；
- 校验 task identity 和来源类型；
- 加载 session headers；
- 通过缓存调用 provider-specific target reader；
- 统一返回 site、task id、display name 和 targets。

`ACPTerminal` 和 `CCITerminal` 继承该基类，只实现各自的 API 路径、任务状态字段、worker/container 过滤和 URL 组装。CCI 的地址附加字段与 `VerifiedCCITargets` 保留在 CCI 子类边界内。`TerminalBridge` 继续负责连接生命周期，不依赖具体云端 API 实现。

IDE、移动端和 runtime profile 模块没有共同的生命周期或可替换协议，继续使用小型 helper 和组合关系；不为了形式上的继承引入无意义的基类。

### 4. Repository visibility and `.gitignore`

测试源码保留在本机用于验证，但从 Git 索引删除并加入 `.gitignore`。覆盖范围包括：

- 根目录 `tests/`；
- 移动端 `src/test/`、`*_test.go` 和 `*Test.java`；
- `test_*.py`、`*_test.py`；
- `*_smoke.py`、`*_check.py`、`*_check.js` 和本地 benchmark 验证脚本；
- 测试缓存、coverage、日志、临时 profile 和本地部署输出。

不忽略生产代码、部署脚本、运行时配置 schema 或用户需要的静态资源。合并前使用 `git ls-files` 检查 `main` 不再跟踪测试文件；本地测试文件不因忽略规则而被删除。

### 5. Deployment contract

部署文档和入口统一指向 `main` 的自动 profile 选择。Air/Mini profile 文件只作为经过验证的本机回滚或显式覆盖材料，不再要求选择某个功能分支才能部署。部署脚本不得读取或上传 `.env`、session、数据库或本地测试产物到 Git。

## Error handling

- 硬件探测失败时使用现有安全 fallback，不阻塞服务启动。
- profile 文件格式、取值范围或硬件签名不匹配时，在任何服务停止和文件复制之前失败。
- 终端基类只负责公共校验和缓存；云端错误继续由现有 `ActionError` 语义映射，避免改变 API 错误码。
- 分支删除只在 PR 合并、`main` 验证通过且远端引用确认后执行。

## Validation

实现完成后必须在本机执行：

1. Python 全量测试和 Air/Mini profile 选择测试；
2. 前端效率测试、Codex context 检查和已有 smoke 脚本；
3. 终端解析器、缓存和 bridge 的回归测试；
4. `git diff --check`；
5. `git ls-files` 测试文件扫描，确认 GitHub 将不包含测试源码；
6. PR 合并后的 `main` 再次验证，并确认所有非 `main` 分支已删除。

## Non-goals

- 不重新设计云端 API，不提高未经实测的云请求并发；
- 不修改 Air/Mini 以外机器的实验性调度参数；
- 不把测试源码发布到 GitHub；
- 不部署到生产环境或修改本机运行配置，除非用户另行要求。
