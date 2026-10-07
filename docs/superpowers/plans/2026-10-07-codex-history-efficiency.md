# Codex history efficiency

Goal: Reduce metadata filtering work without changing current-directory and descendant matching.

Implementation in this session (user has requested direct implementation):
- Add failing regression tests for an empty valid index avoiding JSONL scans, repeated cwd resolution, older project rows beyond 5,000 unrelated threads, and concurrent same-project reads.
- Cache realpath results for each script invocation, filter database rows before the result limit, and treat a readable index with zero matches as authoritative.
- Serialize only same-session/same-workspace history reads and retain four workspace cache entries for 15 seconds.
- Run targeted and full Python tests; compare synthetic metadata workloads before/after.
- Deploy only changed runtime modules with backups, restart the monitor's existing system service, validate API and container history, then commit and push.

Constraints: No chat-body reads, no credential changes, no modification of unrelated pending CSS changes. Keep SQLite read-only and JSONL compatibility when the index is unavailable. Do not extend cache staleness.

## Verification results

Completed on 2026-10-07:
- Regression phase: four new tests failed for duplicate path resolution, rollout scans after an empty valid index, globally truncated project history, and duplicate concurrent container reads. They pass after the change.
- Full suite: 854 passed (five existing websocket deprecation warnings).
- Synthetic benchmark: 5,000 records, seven runs; median script time 65.45 ms before and 2.51 ms after (26.1x). This measures metadata filtering, not network latency.
- Live deployed API: three concurrent project requests returned identical two-record results; subsequent cache hit 5.50 ms; switching to an empty project returned zero records and switching back took 6.47 ms.
- Initial live attempts returned intermittent HTTP 503; direct execution of the same script succeeded for both paths, and the final complete API run passed without retries. First uncached live read still took 9.79 seconds, so container transport remains a latency limit; this change does not claim to fix intermittent cloud failures.
- Deployed only server/cci_ide.py and server/ide_history.py, with runtime backups. Existing system launch service restarted and /api/state returned HTTP 200. Unrelated pending CSS changes are excluded from this commit.
