# 跨站数据传输 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在存储管理页增加可选择执行节点的跨站数据传输工作区，支持 CCI/AFS/AOSS/SSH 端点、sync/copy、任务状态和取消。

**Architecture:** 新增独立的 `server/transfers.py` 任务层，不改变现有 `server/ssh_transfers.py` 的单机 SCP 逻辑。后端提供执行节点和传输任务 API，前端在 `storage` 页面增加“数据传输”标签并通过结构化端点配置创建任务。

**Tech Stack:** FastAPI、asyncio、现有 SSH catalog/command 工具、原生 JavaScript、现有 CSS、pytest、Node test runner。

---

### Task 1: Add the transfer domain model and validation tests

**Files:**
- Create: `server/transfers.py`
- Create: `tests/test_transfers.py`

- [ ] **Step 1: Write failing validation tests**

```python
from server.transfers import normalize_endpoint, choose_operation


def test_afs_endpoint_normalizes_network_and_prefix():
    endpoint = normalize_endpoint({
        "type": "afs", "site": "sensecore", "network": "internal",
        "bucket": "transfer", "path": "weights"
    })
    assert endpoint == {
        "type": "afs", "site": "sensecore", "network": "internal",
        "bucket": "transfer", "path": "weights/"
    }


def test_ssh_endpoint_is_always_external():
    endpoint = normalize_endpoint({"type": "ssh", "alias": "charles_jumper", "path": "/data"})
    assert endpoint["network"] == "external"


def test_directory_transfer_defaults_to_sync():
    assert choose_operation("directory", "") == "sync"
    assert choose_operation("file", "copy") == "copy"
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python -m pytest tests/test_transfers.py -q`

Expected: FAIL because `server.transfers` does not exist.

- [ ] **Step 3: Implement endpoint normalization and operation selection**

Implement `normalize_endpoint`, `normalize_job`, `choose_operation`, and `ActionError` based validation. Accept `cci`, `afs`, and `ssh` endpoints; require `site` and `network` for `afs`; force `network="external"` for `ssh`; require absolute paths for `cci` and `ssh`; normalize AFS directory prefixes with a trailing slash.

- [ ] **Step 4: Run the focused test**

Run: `python -m pytest tests/test_transfers.py -q`

Expected: PASS.

---

### Task 2: Implement executor catalog and background transfer manager

**Files:**
- Modify: `server/transfers.py`
- Modify: `server/ssh.py`
- Modify: `tests/test_transfers.py`

- [ ] **Step 1: Write tests for executor output and command routing**

Cover SSH catalog conversion, CCI executor records, AFS upload/download/sync/copy routing, max concurrent jobs, and cancellation. Mock process creation and executor probing so no real network call is made.

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `python -m pytest tests/test_transfers.py -q`

Expected: FAIL for the missing manager methods.

- [ ] **Step 3: Implement `ExecutorCatalog`**

Return normalized records with `type`, `id`, `label`, `status`, `capabilities`, and `reason`. SSH records come from `SSHCatalog`; CCI records come from the site runtime's available CCI task/instance resolver. Keep CCI discovery behind a provider callback so tests can inject deterministic nodes.

- [ ] **Step 4: Implement `TransferManager`**

Store no more than 100 recent jobs, allow at most three active jobs, run each job as an asyncio task, expose `snapshot`, `list_jobs`, `start`, and `cancel`, and clean staging directories on all exit paths. Use an injected worker factory so command construction can be tested without launching binaries.

- [ ] **Step 5: Implement worker command routing**

Select `ads-upload` for CCI→AFS, `ads-download` for AFS→CCI, `ads-sync` for AFS→AFS with `mode="sync"`, and `ads-copy` for AFS→AFS with `mode="copy"`. Reject unsupported combinations during preflight with a structured user-facing error.

- [ ] **Step 6: Run the focused tests**

Run: `python -m pytest tests/test_transfers.py -q`

Expected: PASS.

---

### Task 3: Register transfer APIs in the application

**Files:**
- Modify: `server/main.py`
- Create: `tests/test_transfer_api.py`

- [ ] **Step 1: Write API tests**

Use the existing app fixture to test `GET /api/transfers/executors`, `POST /api/transfers/browse`, `POST /api/transfers`, `GET /api/transfers`, and `POST /api/transfers/{id}/cancel`. Assert that invalid endpoint JSON returns the existing action error format and that secrets are absent from responses.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_transfer_api.py -q`

Expected: FAIL because the routes are not registered.

- [ ] **Step 3: Wire manager lifecycle into `create_app`**

Create a transfer manager beside `SSHTransfers`, expose it through `app.state`, close it in the lifespan, and register routes using the existing `perform` and `action_body` helpers. Keep transfer routes independent of `X-Monitor-Site`; each AFS endpoint carries its own site.

- [ ] **Step 4: Run API tests**

Run: `python -m pytest tests/test_transfer_api.py -q`

Expected: PASS.

---

### Task 4: Add the storage page transfer workspace

**Files:**
- Modify: `static/index.html`
- Modify: `static/navigation.js`
- Modify: `static/storage.js`
- Modify: `static/storage.css`
- Create: `static/transfer.css`

- [ ] **Step 1: Add the failing frontend behavior test**

Run a structural check over `static/index.html`, `static/storage.js`, and `static/transfer.css` asserting the transfer tab, endpoint fields, executor selector, progress rendering, and job log rendering are present.

- [ ] **Step 2: Run the test to verify failure**

Run: `python - <<'PY'` with assertions for the transfer IDs and API paths.

Expected: FAIL because the transfer markup and script do not exist.

- [ ] **Step 3: Add transfer tab markup**

Add a tablist under `#storage-section`, retain the existing AFS panel, and add a transfer panel containing executor selection, source and destination endpoint cards, browse controls, mode selection, submit button, preflight status, and active-job list.

- [ ] **Step 4: Extend `static/storage.js` with the transfer workspace**

Load executor lists, switch endpoint forms by type, force SSH network text to external, browse paths through the transfer API, submit structured jobs, poll job status once per second while active, and render cancellable jobs. Read API data through `MonitorSite.fetch` only where site-scoped storage data is needed; transfer APIs must use plain fetch without inheriting the current site header.

- [ ] **Step 5: Add responsive styles**

Use the existing panel, form, table, button, and status tokens. On narrow screens stack endpoint cards and make the job table horizontally scrollable without changing existing storage layout.

- [ ] **Step 6: Run the frontend test**

Run: `node --check static/storage.js && node --check static/ssh.js`.

Expected: PASS.

---

### Task 5: Add SSH management shortcuts and documentation

**Files:**
- Modify: `static/ssh.js`
- Modify: `static/index.html`
- Modify: `README.md`
- Create: `docs/2026-10-16-cross-site-transfer-usage-1016-1617.md`

- [ ] **Step 1: Add shortcut behavior**

Add a “跨端传输” action to each SSH card that navigates to `#storage` with the transfer tab selected and the SSH alias prefilled in session state. Keep existing upload/download actions unchanged.

- [ ] **Step 2: Document the workflow**

Document the separation between execution node, source, and destination; explain when `sync` and `copy` are used; include the proven SenseCore↔maoshanwang AFS commands and the external-only SSH rule. Use a timestamped filename for the new usage document and do not rename `README.md`.

- [ ] **Step 3: Run JavaScript and documentation checks**

Run: `node --check static/storage.js && node --check static/ssh.js` and `python -m pytest tests/test_transfers.py tests/test_transfer_api.py -q`.

Expected: PASS.

---

### Task 6: Full verification and review

**Files:**
- Modify only files listed above if verification reveals a defect.

- [ ] **Step 1: Run the focused backend suite**

Run: `python -m pytest tests/test_transfers.py tests/test_transfer_api.py tests/test_ssh_transfers.py tests/test_storage.py -q`

- [ ] **Step 2: Run the full Python suite**

Run: `python -m pytest -q`

- [ ] **Step 3: Run all Node tests**

Run: `for f in tests/*.test.cjs; do node --test "$f" || exit 1; done`

- [ ] **Step 4: Inspect the final diff**

Run: `git diff --check` and `git status --short`; confirm pre-existing user modifications remain intact and no credentials or raw command secrets appear in tracked files.

- [ ] **Step 5: Commit the feature**

```bash
git add server/main.py server/transfers.py static/index.html static/navigation.js static/storage.js static/transfer.css static/ssh.js tests/test_transfers.py tests/test_transfer_api.py docs/2026-10-16-cross-site-transfer-usage-1016-1617.md docs/superpowers/specs/2026-10-16-cross-site-transfer-design.md docs/superpowers/plans/2026-10-16-cross-site-transfer.md
git commit -m "feat: add cross-site data transfer workspace"
```
