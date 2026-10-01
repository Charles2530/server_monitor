# Feishu local relay implementation plan

> **For agentic workers:** Use executing-plans to implement these tasks inline, with test-first verification.

**Goal:** Forward this user's two-site charles task events to the signed Feishu group bot.

**Architecture:** A standalone standard-library Python process reads the dashboard's loopback GET APIs. SQLite atomically persists task baselines and an outgoing queue; a separate LaunchAgent runs a copied version from the internal disk.

**Tech Stack:** Python 3.9+, unittest/pytest, urllib, sqlite3, launchd.

## Global constraints

- No changes to the dashboard, IAM credentials, or cloud tasks.
- Bot credentials only in a mode-0600 config outside the repository.
- Only charles tasks; separate maoshanwang/sensecore labels; official HTTPS task URLs.
- Defaults: GPU below 10% for 30 minutes, 6-hour cooldown, 09:00 UTC+8 daily summary, no free-GPU alerts.
- No initial historical message burst; failed or stale sources cannot generate transitions or extend low-GPU duration.
- Persistent deduplication and bounded retries. A transport timeout can still duplicate a delivered message because the bot API has no idempotency key.

### Task 1: Relay and behavioral tests

Files: `scripts/feishu_relay.py`, `tests/test_feishu_relay.py`.

Interfaces: `Relay(path, config).observe(site, current, history, now)`, `.pending()`, `.flush(sender, now)`, `.daily(snapshots, now)`; `signed_payload(text, secret, now)` and `send_message(config, text)`.

- [x] Write tests for silent startup, transitions, restart dedup, stale data, disappearing tasks, site isolation, metric continuity/cooldown, daily once per day, signing/error responses and persisted retries.
- [x] Run `python -m pytest tests/test_feishu_relay.py -q` and confirm missing implementation failure.
- [x] Implement relay using a SQLite transaction for both state and queued messages, and validate Feishu's application response code.
- [x] Run focused tests and fix only demonstrated failures; inspect complete change.

### Task 2: Install and verify

Files: `scripts/deploy_feishu_relay.py`, `docs/feishu-notifications.md`, README link.

- [x] Copy relay to `~/Library/Application Support/CharlesMonitorFeishu`; retain private config/database across updates.
- [x] Generate `~/Library/LaunchAgents/charles.monitor-feishu.plist` with RunAtLoad, KeepAlive and private log paths.
- [x] Validate plist and run relay once without starting a duplicate daemon.
- [x] Send one explicitly marked signed test message and verify it visibly arrives in the selected group.
- [x] Bootstrap LaunchAgent, confirm running PID, initialized source baselines and dashboard availability; document pause/update commands and limitations.

## Verification record

Signed test returned Feishu code=0 and was visually confirmed in the selected group. LaunchAgent runs from the internal disk; all eight site/source baselines initialized quietly. Review findings for initial-history recovery and retry ordering were reproduced and corrected with regression tests. Credentials remain outside Git with mode 0600.
