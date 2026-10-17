# VS Code 审阅过滤与安全目录修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the existing VS Code review command with automatic Git safe-directory repair and optional file-extension filtering while preserving native review for all files.

**Architecture:** Keep all behavior in the existing workspace extension entrypoint. Add small Git/URI helpers that invoke `git` with `execFile`, collect changed paths for working or staged changes, and write a filtered patch to the extension global storage. Keep the current native commands for the unfiltered path. Extend the existing Node VM harness so command execution and filesystem behavior can be asserted without a real VS Code host.

**Tech Stack:** VS Code extension API, Node.js `child_process.execFile`, Node built-in `node:test`, `assert/strict`.

---

### Task 1: Add failing review workflow tests

**Files:**
- Modify: `tests/context_actions.test.cjs`

- [x] **Step 1: Extend the harness with workspace, Git process, and input-pick fixtures**

Add options for `workspaceRoot`, `changedFiles`, `gitOutputs`, `gitErrors`, `input`, and `open` calls. Stub `vscode.workspace.workspaceFolders`, `vscode.workspace.fs.writeFile`, `vscode.window.showInputBox`, and `vscode.commands.executeCommand` while preserving existing assertions.

- [x] **Step 2: Add a failing test for safe.directory repair**

Assert that a review run first invokes `git config --global --get-all safe.directory`, then invokes `git config --global --add safe.directory <workspaceRoot>` when the root is absent.

- [x] **Step 3: Add a failing test for Python filtering**

Select working changes and Python, provide `.py`, `.pyi`, `.js`, and an untracked `.py` path, then assert the generated patch is opened and excludes the JavaScript path.

- [x] **Step 4: Add failing tests for staged and custom extension filtering**

Assert staged mode invokes cached diff arguments, and custom input such as `PY, .proto` matches case-insensitively after normalization.

- [x] **Step 5: Add a failing test for no matching files**

Assert no `vscode.open` call occurs and a warning is shown when the selected extension has no changed files.

- [x] **Step 6: Run the focused tests and confirm the new tests fail for missing behavior**

Run:

```bash
node --test tests/context_actions.test.cjs
```

Expected: existing tests pass and the newly added review-filter tests fail because the current command only delegates to native Git.

### Task 2: Implement safe-directory repair and filtered diff generation

**Files:**
- Modify: `static/editor-appearance/extension/context.js`

- [x] **Step 1: Add process and path helpers**

Require `child_process.execFile` and add Promise wrappers that return `{stdout, stderr}` while preserving non-zero `git diff --no-index` output for untracked files.

- [x] **Step 2: Resolve the workspace root and repair Git trust**

Resolve the active editor workspace or first workspace folder, run `git -C <root> rev-parse --show-toplevel`, inspect global safe-directory entries, and add the normalized root exactly once when missing.

- [x] **Step 3: Add review scope and file-filter choices**

Use QuickPick for working/staged scope and file type. Keep “全部文件” on the existing native command path. Add preset suffix sets and a custom input path that accepts comma/space-separated extensions, trims values, adds a leading dot, and lowercases them.

- [x] **Step 4: Collect filtered changed paths**

Use `git diff --name-only` for working tracked files, `git diff --cached --name-only` for staged files, and `git ls-files --others --exclude-standard` for working untracked files. Filter by normalized suffix and deduplicate paths.

- [x] **Step 5: Generate and open a temporary filtered patch**

Use the selected diff mode with `--` plus the relative paths. Append `git diff --no-index -- /dev/null <absolute-path>` output for working untracked files. Write the result below `globalStorageUri` and open it with `vscode.open`. Show a warning for empty output or no matching paths.

- [x] **Step 6: Preserve existing error handling**

Catch workspace, Git, safe-directory, input, filesystem, and open failures with concise Chinese warning/error messages. Never run `chown` and never mutate repository files or the Git index.

### Task 3: Verify the implementation

**Files:**
- Modify: `tests/context_actions.test.cjs` only if a test fixture needs correction

- [x] **Step 1: Run the focused Node test file**

Run `node --test tests/context_actions.test.cjs` and require all tests to pass.

- [x] **Step 2: Run JavaScript syntax checks**

Run `node --check static/editor-appearance/extension/context.js` and `node --check tests/context_actions.test.cjs`.

- [x] **Step 3: Run diff whitespace and status checks**

Run `git diff --check` and `git status --short`. Confirm only the requested extension/test files plus the timestamped plan are newly changed; preserve all pre-existing user modifications.

- [x] **Step 4: Commit only the tracked implementation file and plan**

The repository intentionally ignores `tests/`, so the extended local regression fixture remains available for verification without adding the test tree to the product commit.

```bash
git add static/editor-appearance/extension/context.js docs/superpowers/plans/2026-10-17-vscode-review-filter-implementation-1017-1352.md
git commit -m "feat: filter VSCode reviews and repair git trust"
```
