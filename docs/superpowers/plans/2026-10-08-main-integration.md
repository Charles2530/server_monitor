# Main Branch Integration and Adaptive Mac Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Consolidate all existing product functionality into `main`, automatically adapt the measured MacBook Air and Mac mini profiles, reduce terminal resolver coupling, and keep test sources local-only.

**Architecture:** Existing feature branches are already ancestors of `main`; only the current Codex/efficiency commit is pending. Bring it into an integration branch, add exact Air/Mini hardware selection, extract a shared terminal resolver base class, and remove test sources from Git tracking while retaining them locally for validation.

**Tech Stack:** Python 3.9, FastAPI/uvicorn, `httpx`, Node.js built-in test runner, macOS LaunchAgent profiles, GitHub pull requests.

## Global Constraints

- `main` is the only retained branch after the PR is merged.
- Air: `Mac16,12`, arm64, 10 cores, 16 GiB, queue 3, polling 10/10/10, `Standard`.
- Mini: `Mac16,10`, arm64, 10 cores, 24 GiB, queue 6, polling 30/10/15, `Interactive`.
- Other hardware keeps conservative generic defaults.
- Test sources stay locally available but are not tracked by GitHub.
- Do not increase unmeasured cloud request concurrency.
- Do not deploy or change local runtime state during implementation.

---

### Task 1: Prepare integration policy

**Files:** `.gitignore`, `docs/superpowers/specs/2026-10-08-main-integration-design.md`, `docs/superpowers/plans/2026-10-08-main-integration.md`

- [ ] Confirm historical branch tips are ancestors of `main` with `git merge-base --is-ancestor`.
- [ ] Add ignore rules for `tests/`, mobile test directories, `test_*.py`, `*_test.py`, `*_test.go`, `*Test.java`, smoke/check/benchmark scripts, coverage, caches and local logs.
- [ ] Remove matched test sources from the Git index using `git rm --cached`; keep files on disk.
- [ ] Verify `git ls-files` returns no test-source paths and `git check-ignore` matches local copies.
- [ ] Commit with `git commit -m "chore: prepare main integration policy"`.

### Task 2: Make Air and Mini profile selection automatic

**Files:** `server/runtime_profile.py`, `scripts/deploy_launchagent.py`, `config/runtime-profiles/macbook-air-current.json`, `config/runtime-profiles/mac-mini-current.json`; local tests: `tests/test_runtime_profile.py`, `tests/test_launchagent.py`.

- [ ] Add failing tests for exact Air and Mini signatures and a nearby unmeasured machine.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_runtime_profile.py tests/test_launchagent.py` and confirm the new Air assertion fails.
- [ ] Implement exact hardware profiles before generic CPU/memory tiers; preserve schema validation and explicit profile hardware checks.
- [ ] Re-run focused tests and inspect LaunchAgent payloads for `Standard` Air and `Interactive` Mini.
- [ ] Commit with `git commit -m "feat: auto select Air and Mini runtime profiles"`.

### Task 3: Extract the shared terminal resolver workflow

**Files:** create `server/terminal_resolver.py`; modify `server/acp_terminal.py`, `server/cci_terminal.py`; local tests: `tests/test_acp_terminal.py`, `tests/test_cci_terminal.py`, `tests/test_terminal_target_efficiency.py`.

- [ ] Add failing contract tests for shared identity validation, session headers, cache reuse and subclass-specific output.
- [ ] Run the focused terminal tests and confirm failure before production changes.
- [ ] Implement a template-method base class owning construction, validation, headers, cache access and result normalization.
- [ ] Keep CCI address output and `VerifiedCCITargets` inside the CCI subclass; keep ACP worker filtering inside the ACP subclass.
- [ ] Run focused and full local tests, then commit with `git commit -m "refactor: share terminal target resolution workflow"`.

### Task 4: Unify the deployment entry point

**Files:** create `deploy.sh`; modify `README.md`, `scripts/deploy_launchagent.py`, `deploy-air.sh`, `deploy-mini.sh`; local test: `tests/test_launchagent.py`.

- [ ] Add a failing test proving the canonical entrypoint selects `auto`.
- [ ] Implement `deploy.sh` to invoke `scripts/deploy_launchagent.py --profile-file auto`; keep legacy wrappers as compatibility shims or remove them only when unused.
- [ ] Verify generated profiles without invoking `launchctl`, installing dependencies or changing local runtime state.
- [ ] Commit with `git commit -m "feat: provide one adaptive deployment entrypoint"`.

### Task 5: Bring in the current Codex and frontend work

**Files:** current Codex context/IDE/frontend changes from commit `31b4661`; local checks: `scripts/frontend_efficiency_check.js`, Codex context check and IDE tests.

- [ ] Create `codex/main-integration` from the updated current branch while preserving `main` ancestry.
- [ ] Cherry-pick or merge `31b4661`, resolving only repository-cleanup conflicts.
- [ ] Run `node --test scripts/frontend_efficiency_check.js` and `node scripts/codex_context_check.js`.
- [ ] Commit conflict resolutions separately if needed.

### Task 6: Validate, PR, merge and clean branches

**Files:** no planned production files after validation unless a test exposes a defect.

- [ ] Run `.venv/bin/python -m pytest -q`, `node --test scripts/frontend_efficiency_check.js`, `git diff --check`, and the tracked-test scan.
- [ ] Push `codex/main-integration` and create a PR to `main` with a reviewable body.
- [ ] Merge the PR after checks pass, fetch `origin/main`, and check out `main`.
- [ ] Re-run the complete validation from merged `main`.
- [ ] Delete every local and remote branch except `main`; do not force-push.
- [ ] Confirm `git status -sb` is clean and no test source is tracked.
