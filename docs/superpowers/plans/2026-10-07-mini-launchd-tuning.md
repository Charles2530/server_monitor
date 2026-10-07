# Mac mini launchd tuning plan

**Goal:** Apply a measured, machine-specific deployment setting to the main web service on Mac16,10, preserving Pro settings and existing cloud concurrency.

**Evidence:** Main production job uses Background scheduling (priority 4). M4 has 4 performance cores, 6 efficiency cores, 24 GiB RAM. No recorded thermal warning; memory free percentage 40%. Five interleaved rounds of identical isolated launchd workloads show Background / Standard / Interactive median save latency 278.424 / 72.624 / 70.140 ms, state latency 3.447 / 0.831 / 0.755 ms, history latency 41.909 / 2.990 / 2.766 ms. These are synthetic local workloads, not cloud speed measurements. No core residency or power measurement was made.

**Design:** Add optional validated `web_process_type` to runtime profiles, selected as Interactive only for the already verified Mac16,10. Other hardware and old manual profiles retain the Background fallback. The web service handles interactive HTTP/WebSocket requests; it has no XPC activity tracking, so Adaptive is not used. The deployment script renders the chosen value into the launchd plist. No CPU affinity, elevated nice priority, extra worker process, increased cloud concurrency, compression cache or global OS setting.

- [x] Test Mini-only selection, legacy/manual defaults, invalid values, generated plist and daemon migration preservation.
- [x] Implement the optional profile field and extract the existing plist builder for behavior testing.
- [x] Run focused then full regression tests; request independent review of the focused diff.
- [x] Preserve all current profiles and original system plist before deployment. Update only Mini runtime profile, profile loader and main job scheduling. Preserve unrelated changes and other services.
- [x] Reload the main launchd job (kickstart alone does not reload a changed plist), verify Interactive classification, service readiness, code/profile hashes.
- [x] Compare the same local HTTP endpoints before/after; report limits and a concrete rollback backup. Save Mini current profile and preserve both Pro baselines byte-for-byte.

Authorization comes from the user's ongoing request to optimize and deploy specifically for this Mini. No further approval is needed for this scoped reversible deployment.
