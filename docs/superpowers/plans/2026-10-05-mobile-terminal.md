# Mobile Terminal Implementation Plan

> **For agentic workers:** Use executing-plans / subagent-driven-development to implement and independently review the tasks below.

**Goal:** Fix cramped mobile terminal, touch shortcuts and slow HTTP recovery, then deploy locally.
**Architecture:** Keep xterm and existing server-side sessions. Separate touch presentation/input controller from transport. Add optional non-waiting read for recovery only.
**Tech Stack:** Vanilla JS/CSS, xterm 5.5, FastAPI, pytest, Playwright, local PTY.

## Global Constraints

No cloud test writes. Keep devices and credentials unchanged. Never replay unacknowledged input. Retain desktop tools and old APK. User approved design above; execute continuously.

### Task 1: Immediate HTTP recovery

Files: server/terminal_bridge.py, server/terminal_routes.py, tests/test_terminal_bridge.py, tests/test_terminal_routes.py.
- [x] Add red tests for `await bridge.read(key, cursor, owner, wait=False)` returning empty output immediately and current sequence; ownership and invalid boolean preserved.
- [x] Run `.venv/bin/python -m pytest tests/test_terminal_bridge.py tests/test_terminal_routes.py -q` and record expected failure.
- [x] Implement keyword-only `wait=True`; validate `type(wait) is bool`; guard long polling with `if wait and not s.frames and not s.ended`. Route accepts optional `wait`, forwards default true.
- [x] Run tests and review. No commits touching other task files.

### Task 2: Mobile presentation, input and client recovery

Files: static/terminal.html, static/terminal.css, static/terminal.js, new static/terminal-mobile.js/css, scripts/mobile_terminal_smoke.py.
- [x] Add mocked browser red cases for 844×180 >=5 rows, 393×350 >=12 rows, cursor-mode arrow bytes, no blur/synthetic duplicate on touch, no control input while composing, HTTP immediate recovery and no replay.
- [x] Add compact mobile session bar, keyboard toggle, scrollable shortcut strip and an accessible more panel; move existing controls rather than duplicate IDs. Restore desktop DOM placement when leaving mobile layout.
- [x] Use `(pointer: coarse)` plus small viewport and visualViewport dimensions; set viewport height/offset only at scale≈1, coalesce fits with requestAnimationFrame; use CSS grid `auto minmax(0,1fr) auto`, hide verbose chrome in compact mode.
- [x] Route buttons through one input handler, cursor bytes `\x1b${term.modes.applicationCursorKeysMode ? 'O' : '['}${direction}`; prevent pointerdown focus loss except composition; composition blocks control commands. Add one-shot Ctrl and backspace.
- [x] Remember successful HTTP fallback and use `{session_id, cursor, wait:false}` for recovery; input is disabled during recovery and never replayed. Check response sequence for writes; keep original shell.
- [x] Run mobile smoke, existing cci_terminal_smoke and terminal_pty_smoke.

### Task 3: Verify, review and deploy

- [x] Run complete `.venv/bin/python -m pytest tests -q`; mobile smoke plus desktop/local PTY checks and inspect screenshots.
- [x] Independent review entire diff against approved spec; fix critical/important findings with targeted tests.
- [ ] Document limits (no connected physical phone) and usage. Commit implementation, fast-forward local main after verifying clean checkout, never push.
- [ ] Redeploy via documented system rollback → deploy_launchagent → mobile_host install → boot_services install. Check runtime hashes, 5 PIDs, ports, fresh site snapshots, phone pairing counts and Feishu status.
