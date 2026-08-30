# Flow Partial QC Job Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Episode-QC to cache and submit a Flow QC Job covering a non-empty subset of an asset's Episodes without weakening full NAS manifest verification.

**Architecture:** Flow continues to provide the full `asset_manifest` and its full-document digest. `QualityCacheManager._manifest_file_specs()` validates the covered Job Episodes against that full manifest, then emits file specifications only for those Episodes plus `asset_manifest.json`; all existing cache, local-workspace and submission code consumes that selected file list unchanged.

**Tech Stack:** Python 3.11+, pytest, pathlib, JSON/SHA-256, existing `QualityCacheManager` and Flow test doubles.

## Global Constraints

- `job.episodes` is the non-empty covered scope; every covered Episode must exist in `job.asset_manifest`.
- Preserve full `asset_manifest` content and `asset_manifest_sha256`; compare the complete NAS manifest to that digest.
- Verify `relative_path`, `primary_file`, and `checksum_sha256` for every covered Job Episode.
- Download and verify only covered Episode files plus the full `asset_manifest.json`.
- Do not modify Flow, DataCollector, NAS layout, manifest schema, digest algorithm, result-upload layout, or unrelated UI.
- Tests must run against real filesystem cache behavior; no Flow network access is required because the existing fake client returns the claimed Job.

---

### Task 1: Cache a partial Flow QC Job safely

**Files:**

- Modify: `src/episode_qc/platform_workflow.py:726-835`
- Modify: `tests/test_platform_workflow.py` near existing `test_flow_job_is_fully_cached_verified_submitted_and_safely_evicted`

**Interfaces:**

- Consumes: `QualityCacheManager.cache_job(client, job)` and `_manifest_file_specs(job, source_root)`.
- Produces: a ready cache whose state `episode_ids` and copied files match `job["episodes"]`, while its recorded manifest digest remains the full asset digest.

- [ ] **Step 1: Write the failing cache regression**

Add `test_cache_job_accepts_partial_job_coverage_and_copies_only_covered_files`. Build a two-Episode asset through the existing `publish_asset_manifest()` helper; retain both Episodes in `job["asset_manifest"]`, set `job["episodes"]` to only the first Episode, then call `QualityCacheManager(...).cache_job(FakeFlowClient(job), job)`.

Assert all observable behavior:

```python
assert cached["reused"] is False
assert (Path(cached["cache_dir"]) / "episodes/episode_000001/motion.bvh").is_file()
assert not (Path(cached["cache_dir"]) / "episodes/episode_000002").exists()
state = json.loads((Path(cached["cache_dir"]).parent / ".qc-cache.json").read_text())
assert state["episode_ids"] == ["AST-PARTIAL-001-EP0001"]
assert state["asset_manifest_sha256"] == canonical_json_sha256(job["asset_manifest"])
```

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_platform_workflow.py -k partial_job_coverage -q
```

Expected failure: `QualityCacheError: Flow 资产清单与质检任务的 Episode 范围不一致` because current code requires equality between Job and manifest Episode sets.

- [ ] **Step 3: Implement the minimal scoped selection**

In `_manifest_file_specs()`:

```python
if not platform_episodes or "" in platform_episodes or not set(platform_episodes).issubset(manifest_episodes):
    raise QualityCacheError("Flow 资产清单与质检任务的 Episode 范围不一致")

for episode_id in platform_episodes:
    episode = manifest_episodes[episode_id]
    for item in (episode.get("manifest") or {}).get("files") or []:
        # retain the existing safe-path, size, digest and source-file checks
```

Keep all pre-existing full-manifest digest/NAS comparison logic. Do not build a subset manifest or rehash it.

- [ ] **Step 4: Verify GREEN and compatibility**

Run:

```bash
uv run pytest tests/test_platform_workflow.py -k 'partial_job_coverage or flow_job_is_fully_cached_verified_submitted_and_safely_evicted or rejects_wrong_manifest_checksum' -q
uv run pytest tests/test_platform_workflow.py -q
```

Expected: all selected and full platform-workflow tests pass.

- [ ] **Step 5: Final checks and local commit**

Run:

```bash
uv run pytest -q
git diff --check
git status --short
```

Commit only the implementation and test:

```bash
git add src/episode_qc/platform_workflow.py tests/test_platform_workflow.py
git commit -m "feat: support partial Flow QC job caches"
```
