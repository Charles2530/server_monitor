# Codex Access and Scoped Context Implementation Plan

> **For agentic workers:** Execute each task in order with test-first changes and fresh verification after every task.

**Goal:** Restore the full-access Codex defaults for both Air and Mini container homes and make VSCode references pass file, folder, multi-selection, and selected-code scope without reading or expanding content in the appearance extension.

**Architecture:** `server/ide_codex.py` updates only the root `approval_policy` and `sandbox_mode` scalars in the selected `CODEX_HOME/config.toml`, using an atomic write that preserves unrelated TOML, symlinks, comments, and mode. The same setup path is used by Air and Mini. The bundled workspace extension forwards supported VSCode URIs to the native Codex commands, uses the native selection command only for an active nonempty selection, deduplicates Explorer multi-selection, and never enumerates directories or reads file contents.

**Tech Stack:** Python 3.9+ (`tomli` fallback, `pytest`), VSCode extension host JavaScript, Node `node:test`, zip/VSIX bundle generation.

## Global Constraints

- Preserve `auth.json` byte-for-byte and never return credentials or configuration contents.
- Update only root `approval_policy = "never"` and `sandbox_mode = "danger-full-access"`.
- Keep Air and Mini on their existing independent `CODEX_HOME` paths and runtime profiles.
- Forward references as URI/path handles; do not read files or enumerate folders in the extension.
- Keep the native Codex command available and do not auto-submit chat messages.
- Keep the bootstrap script within its existing 20,000-byte optional-installation limit.

---

### Task 1: Lock down permission setup behavior with regression tests

**Files:**
- Modify: `tests/test_ide_codex.py`
- Modify: `tests/test_ide_latency.py`

**Interfaces:**
- Consumes: `prepare_codex`, `ensure_full_access_defaults`, and `bootstrap_script`.
- Produces: explicit `full_access` status and a bootstrap size assertion that remains valid after the implementation is compacted.

- [x] **Step 1: Add/adjust failing assertions**

  Update inspection expectations to include `full_access`; add malformed TOML and a non-regular config case that must return `full_access: False` without replacing the file; retain tests for quoted root keys, multiline strings, missing newline, symlink preservation, file mode, idempotency, and independent Air/Mini homes.

- [x] **Step 2: Run the targeted tests and observe the current failures**

  Run: `.venv/bin/python -m pytest -q tests/test_ide_codex.py tests/test_ide_latency.py::test_connection_bootstrap_can_skip_optional_installation tests/test_ide_latency.py::test_codex_inspection_does_not_install_missing_plugin`

  Expected before the final implementation: the bootstrap size and old exact result assertions fail.

- [x] **Step 3: Add a regression test for failed permission setup**

  Assert that malformed TOML leaves the original bytes unchanged and that `prepare_codex(..., install=False)` returns `{'configured': True, 'installed': ..., 'full_access': False}`.

- [x] **Step 4: Run the new test and verify it fails for the missing/incorrect behavior**

  Run: `.venv/bin/python -m pytest -q tests/test_ide_codex.py -k 'malformed or regular'`

  Expected: a focused failure that identifies the permission setup result or preservation issue.

---

### Task 2: Implement compact, atomic full-access defaults

**Files:**
- Modify: `server/ide_codex.py`
- Test: `tests/test_ide_codex.py`

**Interfaces:**
- Consumes: TOML text and a `CODEX_HOME` directory.
- Produces: `full_access_config(text) -> str`, `ensure_full_access_defaults(codex_home) -> bool`, and `prepare_codex(...)` results containing `full_access`.

- [x] **Step 1: Implement the smallest parser-preserving patch**

  Keep `tomllib`/`tomli` validation, identify only root-level assignments, replace just their scalar values, append missing defaults before the first table, and reject malformed/non-regular/unwritable files. Use a lock, temporary file, fsync, ownership/mode preservation, race check, and `os.replace`; resolve symlinks before writing so the symlink itself remains intact.

- [x] **Step 2: Run the targeted permission tests**

  Run: `.venv/bin/python -m pytest -q tests/test_ide_codex.py`

  Expected: all permission tests pass, including malformed TOML, quoted keys, multiline values, symlink mode, idempotency, and independent homes.

- [x] **Step 3: Verify bootstrap size and optional setup behavior**

  Run: `.venv/bin/python -m pytest -q tests/test_ide_latency.py::test_connection_bootstrap_can_skip_optional_installation tests/test_ide_latency.py::test_codex_inspection_does_not_install_missing_plugin`

  Expected: both pass and the generated optional bootstrap remains under 20,000 bytes.

---

### Task 3: Verify and tighten scoped VSCode reference forwarding

**Files:**
- Modify: `static/editor-appearance/extension/context.js`
- Modify: `static/editor-appearance/extension/package.json`
- Modify: `server/ide_appearance.py`
- Modify: `tests/test_ide_appearance.py`
- Modify: `scripts/codex_context_check.js`

**Interfaces:**
- Consumes: Explorer command arguments `(clickedUri, selectedUris)` and the active editor selection.
- Produces: `ilab.codex.addReference`, `ilab.codex.addFileReference`, `ilab.codex.addFolderReference`, and `ilab.codex.addSelectionReference` commands.

- [x] **Step 1: Add/adjust Node tests for the required scopes**

  Cover active selection routing, explicit file routing, folder URI forwarding without `readFile`/`readDirectory`, Explorer multi-selection deduplication, stale selection exclusion, unsupported schemes, missing Codex extension, and command failure messaging.

- [x] **Step 2: Run the Node tests and verify the desired behavior is represented**

  Run: `node scripts/codex_context_check.js`

  Expected: each scope test passes; no content-read or directory-walk stub is called.

- [x] **Step 3: Keep the extension implementation URI-only**

  Register workspace-host commands, normalize `vscode-remote` URIs to workspace-local file URIs, prefer an active nonempty selection only for the auto/selection commands, forward each unique URI to `chatgpt.addFileToThread`, and never call filesystem APIs.

- [x] **Step 4: Bundle and manifest validation**

  Run: `.venv/bin/python -m pytest -q tests/test_ide_appearance.py` and `node --check static/editor-appearance/extension/context.js`.

  Expected: the bundle contains `context.js`, the JS content type, version-matched manifest, scoped menus, and unchanged appearance defaults.

---

### Task 4: Full verification and review

**Files:**
- Review: all files above and `git diff`

- [x] **Step 1: Run the focused suite**

  Run: `.venv/bin/python -m pytest -q tests/test_ide_codex.py tests/test_ide_appearance.py tests/test_ide_latency.py` and `node scripts/codex_context_check.js`.

- [x] **Step 2: Run the complete suite**

  Run: `.venv/bin/python -m pytest -q`.

  Expected: all tests pass; only the existing websocket deprecation warnings remain.

- [x] **Step 3: Review the final diff and status**

  Run: `git diff --check`, `git diff --stat`, and `git status --short --branch`.

Confirm no deployment, local runtime configuration, `auth.json`, or unrelated files changed.

### Efficiency follow-up

- [x] Cache the static compressed bootstrap modules and appearance bundle for the process lifetime.
- [x] Dispatch independent Explorer URI references concurrently while preserving the URI-only boundary.
- [x] Verify with a cache regression test, a concurrent-dispatch Node test, focused tests, and the complete suite.
