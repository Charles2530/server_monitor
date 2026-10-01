# Feishu Bitable implementation plan

> **For agentic workers:** Execute inline with test-first checks and a final independent review.

**Goal:** Make the user's existing local monitor visible as a persistent Feishu table.

**Architecture:** A standalone Python standard-library synchronizer reads the existing monitor SQLite files in read-only mode and upserts rows to one explicitly configured Feishu table. A private LaunchAgent runs a copy on the internal disk.

**Tech Stack:** Python 3.9, sqlite3, urllib, Feishu Bitable API, launchd.

## Global constraints

No cloud task writes, public website, LLM calls, plaintext secrets in Git, or invented terminal states. Native-app/browser operations follow action-time authorization requirements.

### Task 1 — Read and normalize local data

Files: `scripts/feishu_bitable.py`, `tests/test_feishu_bitable.py`.

- [x] Test direct read-only SQLite access, name filtering, site identity, zero metrics, invalid/stale metrics, disconnected source and missing tasks.
- [x] Implement `load_snapshot(path)`, `task_fields(site, task, snapshot, now)`, and `plan_changes(snapshots, records, now)`.
- [x] Run focused tests and a real local dry-run; confirm no local HTTP API calls or credentials in generated fields.

### Task 2 — Safe table synchronization

- [x] Test pagination, Feishu application errors, upsert/restart idempotency, remote duplicate rejection and create timeout handling.
- [x] Implement official tenant authentication, schema initialization, sequential record reads/updates/creates and bounded cycle retries. Keep secret values out of errors/logs.
- [x] Create/authorize the application and target table after required user confirmation; verify one real write and a second update without duplicate rows.

### Task 3 — Deployment and mobile entry

Files: `scripts/deploy_feishu_bitable.py`, `docs/feishu-bitable.md`.

- [x] Install independent LaunchAgent with private config and logs; preserve state across update.
- [x] Make the monitor table easy to open from the selected Feishu group, verify visible fields and current records.
- [x] Verify process, config permissions, matching deployed source and test suite; document stop/resume and data freshness.

## Progress

Completed on 2026-10-01. Dedicated app published with six necessary Bitable permissions and editor access to the requested base; credentials saved privately. Nine task rows verified via real writes/readback, repeated updates create no duplicates. LaunchAgent running, config 0600, deployed code matches source. View filters blank template rows and hides sync keys, document pinned and entry shared to the requested group. Desktop verified; mobile verification remains with the user. Subsequent GPU-count extension adds eleven cluster rows and changes the deployed interval to 60 seconds; see the extension plan and usage documentation. Full suite: 247 passed. Independent review found no material remaining defect.
