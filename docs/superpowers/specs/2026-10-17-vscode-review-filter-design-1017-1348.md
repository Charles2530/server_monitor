# VS Code 审阅权限修复与文件类型过滤设计

## 目标

改进工作区扩展中的“审阅改动”命令，解决两类问题：

1. Git 因仓库所有者与当前用户不同而拒绝操作时，自动为当前仓库补充 `safe.directory`。
2. 审阅文件数量较多时，允许用户按文件类型筛选，优先查看 Python 等重点文件。

实现只涉及 `static/editor-appearance/extension/context.js` 与现有测试 `tests/context_actions.test.cjs`，继续在当前 `main` 分支上修改。

## 交互设计

执行“审阅改动”后依次选择：

1. 范围：工作区改动或已暂存改动。
2. 文件类型：全部文件、Python、JavaScript/TypeScript、配置与文档、自定义扩展名。

“全部文件”沿用 VS Code 内置 Git 差异视图。选择具体类型后，扩展查询该范围内的变更文件，按扩展名过滤，并生成只包含匹配文件的临时 `.diff` 文件后在 VS Code 中打开。没有匹配项时显示提示，不执行空审阅。

预设类型保持少量且可理解：

- Python：`.py`、`.pyi`
- JavaScript/TypeScript：`.js`、`.jsx`、`.ts`、`.tsx`
- 配置与文档：`.json`、`.jsonc`、`.yaml`、`.yml`、`.toml`、`.ini`、`.md`、`.txt`
- 自定义扩展名：输入逗号或空格分隔的扩展名，自动补全前导点并忽略大小写

## 权限与 Git 处理

扩展先确定当前工作区根目录，并使用参数数组调用 Git，避免拼接 shell 命令。执行审阅前读取当前全局 `safe.directory` 列表；仓库根目录不在其中时执行：

```text
git config --global --add safe.directory <repo-root>
```

如果 Git 查询或审阅仍因权限失败，显示包含仓库路径的错误提示，建议用户处理真实文件权限。扩展不自动执行 `chown`，避免改变共享目录、容器挂载或其他用户的文件所有者。

## 变更收集与临时 diff

- 工作区改动使用 `git diff --name-only` 收集已跟踪的未暂存文件，并补充未跟踪文件。
- 已暂存改动使用 `git diff --cached --name-only`。
- 过滤后调用相应的 `git diff`，通过参数数组传入相对路径。
- 工作区中的未跟踪文件通过 `git diff --no-index -- /dev/null <file>` 生成补丁并合并到临时 diff；命令返回码为 1 时仍视为有差异，其他错误才失败。
- 临时文件写入扩展的 `globalStorageUri`，文件名包含时间与随机后缀，使用 VS Code 文件系统 API 保存。
- 过滤审阅只读临时补丁，不修改仓库文件，也不改变 Git 索引。

## 错误处理

- 没有工作区、没有 Git 扩展、无法解析仓库根目录：显示可操作的警告并返回。
- `safe.directory` 写入失败：继续尝试一次 Git 操作；若仍失败，显示原始失败原因。
- 没有匹配文件：提示当前范围没有所选类型的改动。
- 临时补丁写入或打开失败：提示审阅文件生成失败，不删除用户文件。
- 自定义扩展名为空或无效：提示重新输入。

## 测试设计

在 `tests/context_actions.test.cjs` 中扩展现有 harness，覆盖：

1. 原生全部文件审阅仍调用原有 Git 命令。
2. 选择 Python 过滤时只生成并打开过滤后的 diff。
3. 已暂存过滤使用 cached diff。
4. 自定义扩展名规范化并过滤大小写混合路径。
5. 仓库不在 safe.directory 时先添加一次配置。
6. 没有匹配文件时不执行打开命令并显示提示。
7. Git、工作区或临时文件错误不抛出未处理异常。

先写会失败的行为测试，再写最小实现；最后运行针对性 Node 测试、JavaScript 语法检查和 `git diff --check`。
