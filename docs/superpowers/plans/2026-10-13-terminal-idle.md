# Terminal Idle Repair Implementation Plan

Goal: keep idle cloud shells connected without injecting input, and retain diagnostic reasons when they do close.

Architecture: reuse Session's heartbeat and input lock; use channel-4 current dimensions only for cloud sessions. Frontend connection logs retain a fixed allowlist of close messages.

Constraints: main branch only; tests remain ignored; no Mini tuning or VS Code changes; diagnostic cloud shells execute no commands and must be cleaned up.

- [x] Add tests/test_terminal_keepalive.py covering idle expiry, resizing, sequence replay, busy sends, failed heartbeat and SSH opt-out. Run it against current code and confirm missing keepalive failures.
- [x] Modify server/terminal_bridge.py with cloud-only current-size heartbeat. Preserve browser detach expiry and input locks.
- [x] Add privacy regression and browser fixed-message mapping checks, then implement bounded terminal diagnostics in server/terminal_bridge.py and static/connection-log.js.
- [x] Run terminal bridge/routes/runtime and editor smoke regressions; inspect diff and run syntax checks.
- [x] Finish the live idle comparison, review changes, back up main runtime, deploy main service, restore local registry configuration and verify running process/profile/endpoints. Revalidate new server-side keepalive with an idle diagnostic shell if comparison supports it.

Verification before deployment:
- 108 targeted tests passed; 5 existing WebSocket deprecation warnings. Tests remain local and ignored.
- Browser smoke passed: terminal connection, fixed close reasons, reconnect, privacy, 320–1440px widths. A stale fullscreen control in the older smoke script required a focused terminal smoke.
- Regression reproduced input timeout during an already blocked heartbeat, then passed with bounded extra wait. Cancellation and detached browser expiry also pass.
- Independent review confirmed the input-deadline correction with no remaining concrete findings.
- Live A/B: baseline closed after 301.6 seconds; resize keepalive open at 602 seconds; both diagnostic shells cleaned up.
- Deployment changes only server/terminal_bridge.py and static/connection-log.js. Runtime configuration and profile hashes preserved; main and mobile health endpoints return HTTP 200.
- GitHub advanced to 35ca446 during validation. Main fast-forwarded without conflicts; terminal regression rerun on that base. Deployment intentionally remains the existing runtime plus the two terminal fix files; unrelated upstream dashboard changes are outside this terminal repair.
- Deployed automatic keepalive validation: the same isolated cloud shell remained open for 421 seconds with zero client resize frames and zero stdin. Diagnostic shell cleanup completed. Main service health sampled 30 times: median 5.1 ms, maximum 10.5 ms.
