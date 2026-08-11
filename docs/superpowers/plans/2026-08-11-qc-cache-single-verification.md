# QC Cache Single-Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Remove the repeated local re-hash of Episode primary files after a Flow asset is cached, while preserving one complete SHA-256 verification per manifest file and showing real verification progress in the QC task center.

**Architecture:** `QualityCacheManager` will make the manifest-wide verification pass return a normalized `relative_path -> verified SHA-256` mapping. Primary-file validation will consume that completed mapping instead of reading the same cached MCAP/BVH files again. The web application will retain verification progress for only actively caching jobs, expose it through `/api/platform/jobs`, and the existing browser event stream/polling will render `校验 N/M 个文件` while Flow intentionally remains at 99% until the atomic cache promotion succeeds.

**Tech Stack:** Python 3.11+, pytest, standard-library HTTP/SSE server, vanilla JavaScript.

## Global Constraints

- The user explicitly authorized work in the current shared checkout; do not create an isolated branch or worktree.
- A fresh cache attempt may read each source file from the NAS at most once; resumed local partial files may be read locally for integrity checking.
- Every final cache file, including `asset_manifest.json`, must still have its size and SHA-256 checked against the Flow manifest before `.qc-cache.json` is written or the cache directory is atomically promoted.
- The cache may report `cache_ready` / 100% to Flow only after all verification, state-file writing, and `os.replace(partial_job_root, ready_job_root)` succeed.
- Primary-file hashes must be validated from the completed manifest-verification mapping; this optimization must not weaken failure detection or retry behavior.
- Do not alter Flow APIs, NAS source files, manifest format, cache directory layout, or result-upload behavior.
- Keep the existing 99% Flow cache-progress cap during local verification; present its detailed progress only through the QC local web API/UI.

---

## File Structure

- Modify `src/episode_qc/platform_workflow.py`: produce the one-pass manifest verification map, emit per-file verification progress, and validate episode primaries from that map.
- Modify `src/episode_qc/web_server.py`: retain progress for active platform-cache workers and merge it into the local platform-jobs payload.
- Modify `app/renderer/renderer.js`: consume existing SSE payloads and polling payloads to label the verification phase and file ordinal.
- Modify `tests/test_platform_workflow.py`: prove each final cached file is SHA-256 read once per cache pass and verification events are monotonic.
- Modify `tests/test_web_server.py`: prove active verification state is returned by the local API and the browser source renders it.

### Task 1: Make cache verification single-pass and observable

**Files:**
- Modify: `src/episode_qc/platform_workflow.py:280-286,835-925`
- Test: `tests/test_platform_workflow.py`

**Interfaces:**
- Consumes: Flow manifest entries with `relative_path`, `size_bytes`, and `sha256`, plus `job["episodes"]` primary-file metadata.
- Produces: `_verify_manifest_files(asset_root, files, progress_callback=None) -> dict[str, str]` and `_verify_episode_primary_files(job, verified_files) -> list[dict]`.

- [x] **Step 1: Write failing cache verification tests**

  Add a two-Episode fixture and a SHA call counter that records only paths under `qc-cache/downloading`. Assert a completed `cache_job()` calls `sha256_file` exactly once for each manifest `relative_path`, including both primary files, and never calls it a second time for a primary file. Capture progress events and assert their verification entries are ordered and complete.

  ```python
  verification = [item for item in progress if item.get("phase") == "verifying"]
  assert [item["verified_files"] for item in verification] == list(range(1, len(files) + 1))
  assert all(item["total_files"] == len(files) for item in verification)
  assert primary_hash_calls == {"episodes/episode_000001/motion.bvh": 1,
                                "episodes/episode_000002/motion.bvh": 1}
  ```

- [x] **Step 2: Run the focused test to verify it fails**

  Run: `.venv/bin/pytest tests/test_platform_workflow.py -k 'single_pass or fully_cached' -q`

  Expected: FAIL because the current primary validation invokes `sha256_file(primary)` after the manifest-wide verifier and no `phase="verifying"` events exist.

- [x] **Step 3: Return the verified mapping and emit verification state**

  Change the verifier to hash each checked cache file once, reject invalid files exactly as before, add the verified digest to the returned mapping, and publish after every successful file:

  ```python
  verified[str(relative.as_posix())] = digest
  self._emit(progress_callback, {
      "status": "verifying", "phase": "verifying", "progress": 99,
      "verified_files": index, "total_files": len(files),
      "current_file": relative.as_posix(),
  })
  ```

  Pass the returned mapping to primary validation in both fresh-cache and ready-cache reuse flows. Replace the primary-file `sha256_file(primary)` call with a mapping lookup; preserve the existing missing-primary and expected-checksum errors.

- [x] **Step 4: Run focused tests to verify the implementation**

  Run: `.venv/bin/pytest tests/test_platform_workflow.py -k 'single_pass or fully_cached' -q`

  Expected: PASS; the final cache remains atomically promoted only after every manifest file verifies.

- [x] **Step 5: Commit the independently tested cache core**

  ```bash
  git add src/episode_qc/platform_workflow.py tests/test_platform_workflow.py
  git commit -m "perf: verify QC cache files once"
  ```

### Task 2: Preserve and render live local verification progress

**Files:**
- Modify: `src/episode_qc/web_server.py:296-300,530-560,590-700`
- Modify: `app/renderer/renderer.js:155-156,405-440`
- Test: `tests/test_web_server.py`

**Interfaces:**
- Consumes: progress dictionaries emitted by `QualityCacheManager.cache_job`, specifically `phase`, `verified_files`, `total_files`, `current_file`, and `progress`.
- Produces: active job payload field `local_progress: dict[str, object] | None` and browser labels `校验 {verified_files}/{total_files} 个文件`.

- [x] **Step 1: Write failing web-progress tests**

  Add a direct `EpisodeQcWebApplication` payload test that sets an active job plus:

  ```python
  {"status": "verifying", "phase": "verifying", "progress": 99,
   "verified_files": 3, "total_files": 9,
   "current_file": "episodes/episode_000003/data.mcap"}
  ```

  Assert `/api/platform/jobs` carries that dictionary only while `local_caching` is true. Add static renderer assertions for the `phase === "verifying"` branch and the Chinese `校验` file-count label.

- [x] **Step 2: Run the focused test to verify it fails**

  Run: `.venv/bin/pytest tests/test_web_server.py -k 'verification_progress' -q`

  Expected: FAIL because local progress is currently discarded and renderer only displays `缓存 99%`.

- [x] **Step 3: Retain worker progress and render phase-aware labels**

  Initialize `self._platform_progress: dict[str, dict[str, object]]`. In `_cache_platform_job.publish_progress`, copy each event into that map under `_platform_lock` before publishing it. In `_platform_payload`, attach a copied `local_progress` only for codes still in `_platform_jobs`; remove it in the worker `finally` block.

  Route all SSE messages through a wrapper that handles `payload.type === "platform_job"` by updating the matching active `state.platform.jobs` item and re-rendering. Add a helper equivalent to:

  ```javascript
  function flowJobProgressText(job) {
    const local = job.local_progress || {};
    if (local.phase === "verifying" && Number(local.total_files) > 0) {
      return ` · 校验 ${Number(local.verified_files || 0)}/${Number(local.total_files)} 个文件`;
    }
    return ` · 缓存 ${Number(job.cache_progress || local.progress || 0)}%`;
  }
  ```

  Use this helper in both the row detail and disabled action label while the cache worker is active; retain all existing ordinary episode-cache event behavior.

- [x] **Step 4: Run focused tests to verify the implementation**

  Run: `.venv/bin/pytest tests/test_web_server.py -k 'verification_progress or web_claims_caches' -q`

  Expected: PASS; a page refresh during verification obtains the same local verification counters through polling.

- [x] **Step 5: Commit the independently tested presentation layer**

  ```bash
  git add src/episode_qc/web_server.py app/renderer/renderer.js tests/test_web_server.py
  git commit -m "feat: show QC cache verification progress"
  ```

### Task 3: Verify integration and safely load the fix locally

**Files:**
- Modify: `docs/superpowers/plans/2026-08-11-qc-cache-single-verification.md`
- Verify: `src/episode_qc/platform_workflow.py`, `src/episode_qc/web_server.py`, `app/renderer/renderer.js`

**Interfaces:**
- Consumes: the completed cache and UI changes from Tasks 1 and 2.
- Produces: verified local QC code ready for the next cache job; the already-ready task is not re-copied or modified.

- [x] **Step 1: Run the affected integration suites**

  Run: `.venv/bin/pytest tests/test_platform_workflow.py tests/test_web_server.py -q`

  Expected: PASS with cache integrity, Flow worker, HTTP payload, and renderer static contracts green.

- [x] **Step 2: Run repository safety checks**

  Run: `.venv/bin/python -m compileall -q src/episode_qc && git diff --check && .venv/bin/pytest -q`

  Expected: all commands exit 0; no whitespace errors and no unrelated test regression.

- [x] **Step 3: Check the running task is no longer caching before restart**

  Run: `curl -fsS http://127.0.0.1:8765/api/platform/jobs -H "X-Episode-QC-Token: <local-token>"`

  Expected: the prior job has a local task and `local_caching: false`; never restart while a cache worker is copying or verifying.

- [x] **Step 4: Restart only the local Episode-QC web process and confirm health**

  Stop the existing local `episode-qc web` process, start the same command on `127.0.0.1:8765`, then open the local page and request `/api/platform/jobs`. Re-login only if the in-memory Flow session was intentionally cleared by the restart; persistent QC data and cached files must remain intact.

- [x] **Step 5: Mark verification in this plan and commit the final checklist**

  ```bash
  git add docs/superpowers/plans/2026-08-11-qc-cache-single-verification.md
  git commit -m "docs: record QC cache verification"
  ```
