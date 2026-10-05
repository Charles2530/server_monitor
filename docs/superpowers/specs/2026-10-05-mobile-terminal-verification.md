# Mobile terminal verification — 2026-10-05

Implementation keeps the existing xterm and server-side Shell, adds compact touch controls, and restores HTTP sessions with an immediate state read. Desktop tools and the existing APK remain compatible.

## Evidence

- Backend regression was red before `wait=False` support (3 failed, 22 passed), then green (25 passed). Full project suite: 501 passed, 5 existing WebSocket deprecation warnings.
- Mobile browser regression passes touch focus, single key emission, normal/application cursor arrows, one-shot Ctrl, simulated IME candidate ordering, independent visualViewport shrink/offset, original-shell HTTP recovery, and no replay after an input ACK is lost.
- Ctrl+3 regression reproduced an erroneous XOFF byte; explicit digit/punctuation mappings fix it. Unsupported printable characters are preserved. The lost-ACK fixture targets nonempty input, avoiding accidental failure injection into resize frames.
- Existing CCI/ACP browser smoke passes both sites, target selection, ANSI/Chinese rendering, input/resize, and HTTP fallback.
- Local PTY/tmux smoke passes Unicode wrapping, alternate screen, resize, copy mode, font/fullscreen changes, mobile layout, frozen-page keepalive, same-shell reconnect, lost input ACK, and HTTP fallback recovery.
- Independent read-only review found the Ctrl mapping issue; it was corrected and re-reviewed with no remaining production-code blockers.

## Visible terminal rows in Chromium touch simulation

| Visible area | Before | After |
| --- | --- | --- |
| 393 × 780 | 24–27 | 40 |
| 393 × 350 | 3 | 15 |
| 844 × 390 | 12 | 17 |
| 844 × 180 | 1 | 5 |

Screenshots were visually inspected. Browser fixtures mock all cloud requests; PTY tests use an isolated local tmux service. No real cloud commands, task stops, restarts or creations were used for validation.

No physical phone was connected. Browser/CDP IME simulation does not establish compatibility with every Android/iOS keyboard, and this change cannot prevent an underlying network or cloud Shell failure. A service restart ends in-memory terminal sessions; stored IAM sessions, databases and mobile pairing are preserved by deployment.

## Local deployment

Deployed implementation commit `933ce7b` on 2026-10-05 using the existing system LaunchDaemon update flow. Verified served HTML/JS/CSS and deployed Python hashes against source; five system services have running PIDs and no duplicate user jobs; ports 8766 and 8767 each have one loopback listener. Both sites report fresh connected snapshots, all four Feishu tables report fresh successful syncs, and mobile pairing counts remain one device and zero pending pairings. No APK reinstall or GitHub push was performed.
