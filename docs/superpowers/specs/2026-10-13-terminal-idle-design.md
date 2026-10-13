# Cloud terminal idle connection repair

Scope: terminal idle disconnects only. Retain the Mini profile, SSH behavior, editor implementation and no-replay input guarantees.

Observed: three idle cloud sessions ended after 390, 309 and 416 seconds; local service stayed up. Existing cloud protocol pings run every 20 seconds, but no terminal channel traffic is sent during idle time. A live no-command A/B test reproduced an upstream channel-3 interruption after 301.6 seconds with protocol pings only; a second shell receiving identical-size channel-4 frames remained open for 602 seconds. This supports application-level idle expiry on the tested cloud route.

Design: cloud sessions retain the latest successfully sent PTY dimensions. After 45 seconds without a successful write, the existing heartbeat loop may resend that channel-4 control frame. Skip when input holds the write lock; bound control sends to 2 seconds and reserve this bounded wait in the cloud input deadline (12 seconds total, below the browser 15-second ACK deadline); do not send stdin, consume input sequence numbers, renew local browser leases, or close a shell just because a keepalive fails. SSH sessions opt out. Keep detached-session expiry at 15 minutes.

Diagnostics: expose fixed terminal close categories and record UTC time, elapsed seconds and numeric WebSocket close code in server warnings, excluding content, commands, tokens, task IDs and URLs. Preserve these fixed categories in the browser connection log.

Validation: failing regression first; accelerated application-idle fixture; resize/input ordering and cancellation; privacy and log mapping; real no-command idle comparison for 10 minutes; deploy only after tests, preserving registry override. Existing user requested autonomous implementation without review gates.
