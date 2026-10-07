# Mobile Task Workspace Implementation Plan

> Execute inline in the isolated worktree.

**Goal:** Keep phone navigation centered on the selected task, with immediate access to logs and terminal.
**Architecture:** A small shared task-context script validates URL/session state; app.js consumes initial state; mobile.js owns responsive detail navigation; terminal/IDE back links use the same safe return builder.
**Tech Stack:** Existing vanilla JS/CSS and Playwright/Python fixtures.

## Global constraints
No cloud task writes, no new proxy permission, no APK replacement, no uncommitted shared-workspace files in the commit. Both site identities remain explicit.

### Task 1 — Context and route lifecycle
- [x] Add fixture browser checks for fresh root routes, explicit task return and missing task (scripts/mobile_task_workspace_smoke.py). Run `.venv/bin/python scripts/mobile_task_workspace_smoke.py` and verify the bare mobile URL fails against baseline.
- [x] Add static/task-context.js with read/remember/returnUrl functions and defensive sessionStorage handling. Initial context is constrained to task IDs present in the current site's snapshot, kind in all/CCI/ACP, hours in 1/24/168, view in monitor/logs.
- [x] Load context before navigation/app and tools; default only missing mobile hash to tasks. Add app restoration hooks and site-switch clearing.

### Task 2 — Compact task navigation
- [x] Keep native monitor/log tabs reachable; move terminal action into a compact task navigation strip only in mobile mode and restore its original position on desktop.
- [x] Preserve URL task/view and session filters, restore detail on explicit return, handle list/back history, missing target and coarse landscape. Existing terminal transport is untouched.
- [x] Update same-origin terminal and IDE return links, and add mobile workspace CSS that also withstands old APK CSS injection.

- [x] Add direct CCI card terminal entry and keep all seven global navigation entries.

### Task 3 — Regression and integration
- [x] Run new fixture, existing mobile UI, mobile terminal, navigation, and relevant CCI/ACP checks; inspect screenshots at portrait/landscape/dark. Run tests under tests/ only.
- [x] Request read-only review and correct blockers: time range, global navigation scroll, hidden log polling.
- [ ] Commit scoped changes, integrate against current main without overwriting concurrent work, deploy compatible assets and verify served bytes/service health.
