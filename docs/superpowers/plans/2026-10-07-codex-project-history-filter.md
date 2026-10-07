# Codex Project History Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Read Codex session metadata inside each CCI container and filter the embedded Codex history to the current workspace and its descendants.

**Architecture:** Add a read-only container script and IDE API action that returns bounded session metadata. The IDE page requests the metadata after the editor target is ready and sends it to the injected frame script; the frame filters native thread entries when IDs are available and otherwise renders a small project-history layer without guessing from titles. Existing editor recovery and Continue behavior remain unchanged.

**Tech Stack:** Python/FastAPI, temporary CCI terminal bridge, SQLite/JSONL metadata parsing, vanilla JavaScript/CSS, existing pytest and browser smoke scripts.

## Global Constraints

- Read only the container `CODEX_HOME`; do not use the host `~/.codex` in production.
- Match only the normalized workspace root or paths below it; parent-only and pathless sessions are hidden by default.
- Never return chat content, prompts, model output, tokens, cookies, auth files, or environment values.
- Bound command time, output size, record count, field lengths, and database/JSONL work.
- A history-filter failure must leave the editor usable and be recorded in the existing connection log.
- Preserve existing task/container/origin isolation and editor automatic recovery.

---

### Task 1: Add bounded container metadata reader

**Files:**
- Create: `server/ide_history.py`
- Test: `tests/test_ide_history.py`

**Interfaces:**
- `history_script(workspace: str) -> str` returns a self-contained Python script for the CCI shell.
- `sanitize_history_result(value: object, workspace: str) -> dict` returns `{workspace, records, error}` with only safe metadata.
- `workspace_contains(root: str, cwd: str) -> bool` performs normalized root/descendant matching.

- [ ] **Step 1: Write failing path and sanitization tests**

Cover exact root, nested descendants, similar prefixes, empty/relative paths, record caps, long titles, and removal of content-like keys.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `python3 -m pytest -q tests/test_ide_history.py`
Expected: collection or assertion failures because the module is not implemented.

- [ ] **Step 3: Implement the reader**

The remote script must locate the absolute `CODEX_HOME` or `Path.home()/'.codex'`, query only `local_thread_catalog` columns when `sqlite/codex-dev.db` is readable, then fall back to bounded `session_meta` parsing in `sessions/**/*.jsonl`. Normalize `cwd` with `realpath`, apply `workspace_contains`, sort by numeric update time, and emit a single bounded JSON marker. The host sanitizer must reject malformed records and retain only `thread_id`, `display_title`, `updated_at`, and `cwd`.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest -q tests/test_ide_history.py`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add server/ide_history.py tests/test_ide_history.py
git commit -m "feat: read filtered Codex history metadata"
```

### Task 2: Expose the container reader through the IDE API

**Files:**
- Modify: `server/cci_ide.py`
- Modify: `server/ide_routes.py`
- Test: `tests/test_cci_ide.py`
- Test: `tests/test_ide_history_api.py`

**Interfaces:**
- Add `POST /api/cci/ide/history` with `{task_id, instance, container}`.
- Add `CCIEditor.history(data, origin) -> dict` using the existing verified target, temporary shell, owner, timeout, and cleanup rules.

- [ ] **Step 1: Add endpoint contract tests**

Assert validation rejects missing or mismatched task/container fields, successful responses are origin-bound, and a failed container read returns a stable error without exposing shell output.

- [ ] **Step 2: Run the focused API tests and confirm failure**

Run: `python3 -m pytest -q tests/test_cci_ide.py tests/test_ide_history_api.py`
Expected: the new route/action assertions fail.

- [ ] **Step 3: Implement the action and route**

Reuse the same CCI bridge and target verification as editor bootstrap. Pass the editor workspace to `history_script`, run it with a short timeout and bounded output, sanitize the result, and cache it briefly by `(task_id, instance, container, workspace)`. Never include the temporary command output in an API response.

- [ ] **Step 4: Run focused API tests**

Run: `python3 -m pytest -q tests/test_cci_ide.py tests/test_ide_history_api.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add server/cci_ide.py server/ide_routes.py tests/test_cci_ide.py tests/test_ide_history_api.py
git commit -m "feat: expose container Codex history metadata"
```

### Task 3: Add IDE-page history request and frame message

**Files:**
- Modify: `static/ide.js`
- Modify: `static/ide.html`
- Modify: `static/ide.css`
- Test: `tests/test_ide_history_ui.py`

**Interfaces:**
- `POST /api/cci/ide/history` is requested only after `connection.ready` and a verified `connection.workspace` exists.
- The frame message is `{type: 'ilab-codex-history', workspace, records, error}`.

- [ ] **Step 1: Add UI-state tests**

Assert history is requested once per connection, reset on task/container change, retried by the existing connection retry path, and errors update `MonitorConnectionLog` without changing editor status to failed.

- [ ] **Step 2: Run the focused UI tests and confirm failure**

Run: `python3 -m pytest -q tests/test_ide_history_ui.py`
Expected: assertions fail because no history request/message exists.

- [ ] **Step 3: Implement the request flow**

Store `workspace` from the open/recover result, call the new endpoint with the same task/instance/container identity checks, and post the bounded metadata to the frame only after it loads. Clear old records on reconnect or target change. Keep failures non-blocking and note `history_filter_unavailable` in the connection log.

- [ ] **Step 4: Run focused UI tests**

Run: `python3 -m pytest -q tests/test_ide_history_ui.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add static/ide.js static/ide.html static/ide.css tests/test_ide_history_ui.py
git commit -m "feat: send project history metadata to editor"
```

### Task 4: Filter the embedded Codex history safely

**Files:**
- Modify: `static/ide-frame-status.js`
- Modify: `static/ide.css`
- Test: `scripts/ide_history_filter_smoke.py`

**Interfaces:**
- The frame listens for `ilab-codex-history` only from the parent origin.
- It emits `{type: 'ilab-codex-history-state', state: 'ready'|'unavailable'|'empty'}` to the parent.

- [ ] **Step 1: Add browser smoke fixtures**

Create fixtures for history rows with stable thread IDs, rows without IDs, empty metadata, and unavailable metadata. Assert parent-origin checks, exact filtering, descendant retention, similar-prefix exclusion, and the unclassified toggle.

- [ ] **Step 2: Run the smoke script and confirm failure**

Run: `python3 scripts/ide_history_filter_smoke.py`
Expected: the new state assertions fail.

- [ ] **Step 3: Implement native filtering and fallback layer**

Observe only the Codex history region. Prefer stable ID attributes or accessible identifiers when present. If no stable IDs are available, render a compact project-history layer from returned metadata and do not infer membership from title alone. Keep the native workbench functional when the layer cannot attach. Use a MutationObserver with debouncing and disconnect it when the frame is unloaded.

- [ ] **Step 4: Run the smoke script**

Run: `python3 scripts/ide_history_filter_smoke.py`
Expected: all assertions pass.

- [ ] **Step 5: Commit**

```bash
git add static/ide-frame-status.js static/ide.css scripts/ide_history_filter_smoke.py
git commit -m "feat: filter Codex history by project workspace"
```

### Task 5: Run regression verification and deploy

**Files:**
- Modify: `README.md` or a dated verification note only if the repository's deployment workflow requires documentation.
- Test: existing IDE, terminal, and browser smoke suites.

- [ ] **Step 1: Run targeted regression tests**

Run: `python3 -m pytest -q tests/test_ide_history.py tests/test_cci_ide.py tests/test_ide_proxy_http.py tests/test_ide_recovery.py tests/test_ide_entry.py tests/test_ide_setup_status.py`
Expected: all pass.

- [ ] **Step 2: Run existing IDE smoke checks**

Run: `python3 scripts/ide_continue_smoke.py && python3 scripts/ide_auto_repair_smoke.py && python3 scripts/ide_network_smoke.py && python3 scripts/cci_ide_smoke.py`
Expected: all pass without changing existing Continue or reconnect behavior.

- [ ] **Step 3: Run the full relevant suite**

Run: `python3 -m pytest -q`
Expected: no new failures; record any pre-existing unrelated failure separately.

- [ ] **Step 4: Review the diff and deployment state**

Run: `git diff --check HEAD~5..HEAD` and `git status --short`. Confirm unrelated working-tree files remain untouched and no session contents or credentials are staged.

- [ ] **Step 5: Commit verification notes and deploy if checks pass**

Use the repository's existing launch-agent deployment command only after tests pass, then reopen the IDE page and verify one project-root and one subdirectory session in the real CCI container. Do not run cloud write operations.
''')
