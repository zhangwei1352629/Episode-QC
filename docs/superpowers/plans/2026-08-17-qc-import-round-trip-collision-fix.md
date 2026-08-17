# QC Import Round-Trip and Collision Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve exported annotation attributes and reviewer fields during task import, and prevent identical annotation IDs from moving annotations across tasks.

**Architecture:** Normalize exported field aliases before calling the existing annotation validator. Resolve every imported annotation ID from the exported Episode identity and destination Episode: retain it only when both identities match, otherwise derive a deterministic Episode-scoped ID so repeated imports remain idempotent without touching another task.

**Tech Stack:** Python 3.11, sqlite3, pytest, existing Episode-QC workspace/export APIs

## Global Constraints

- Do not add dependencies or change the database schema.
- Preserve current result-file discovery and per-annotation warning behavior.
- Do not modify unrelated working-tree changes.
- This plan does not implement embedded label-schema installation, id-less-row idempotency, rescan precedence, or UI warning display.

---

### Task 1: Round-trip exported attributes and reviewer

**Files:**
- Modify: `tests/test_workspace_v1.py:432-468`
- Modify: `src/episode_qc/workspace.py:748-842`

**Interfaces:**
- Consumes: exported annotation fields `attributes_json: str` and `reviewer: str`
- Produces: `_normalize_imported_annotation(item: dict[str, object]) -> dict[str, object]`

- [x] **Step 1: Extend the round-trip test with fields currently lost**

Add `attributes` and `reviewer_name` to the annotation created in `test_v1_import_restores_existing_annotations_json`, then assert both values after restoring:

```python
annotation = save_annotation(
    db_path,
    {
        # existing fields remain unchanged
        "attributes": {"camera_state": "blurred"},
        "reviewer_name": "质检员甲",
    },
)

assert detail["annotations"][0]["attributes"] == {"camera_state": "blurred"}
assert detail["annotations"][0]["reviewer_name"] == "质检员甲"
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/episode-qc-uv-cache uv run pytest tests/test_workspace_v1.py::test_v1_import_restores_existing_annotations_json -v
```

Expected: FAIL because restored `attributes` is `{}` and restored `reviewer_name` is empty.

- [x] **Step 3: Normalize the exporter aliases before validation**

Add the following helper near `_extract_annotations_for_import` and call it at the start of `_restore_task_annotation`:

```python
def _normalize_imported_annotation(item: dict[str, object]) -> dict[str, object]:
    value = dict(item)
    if "attributes" not in value and "attributes_json" in value:
        raw_attributes = value["attributes_json"]
        if not isinstance(raw_attributes, str):
            raise ValueError("attributes_json 必须是 JSON 字符串")
        attributes = _loads(raw_attributes, None)
        if not isinstance(attributes, dict):
            raise ValueError("attributes_json 必须解析为对象")
        value["attributes"] = attributes
    if "reviewer_name" not in value and "reviewer" in value:
        value["reviewer_name"] = str(value.get("reviewer") or "")
    return value
```

Use the normalized value for path lookup, label lookup, validation, reviewer, and timestamps so the import has one canonical payload.

- [x] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2.

Expected: PASS with the exact custom attributes and reviewer restored.

---

### Task 2: Isolate duplicate annotation IDs between tasks

**Files:**
- Modify: `tests/test_workspace_v1.py` after the existing annotation restore test
- Modify: `src/episode_qc/workspace.py:757-849`

**Interfaces:**
- Consumes: target `episode_id: str`, exported `source_episode_id: str`, and requested `annotation_id: str`
- Produces: `_resolve_import_annotation_id(connection: sqlite3.Connection, episode_id: str, source_episode_id: str, annotation_id: str) -> str`

- [x] **Step 1: Add a two-task collision regression test**

Create a result file once, place identical copies under `task_a` and `task_b`, then import both into the same destination database:

```python
first = scan_data_source(restore_db, source_a)
second = scan_data_source(restore_db, source_b)

first_detail = episode_detail(restore_db, first["episodes"][0]["id"])
second_detail = episode_detail(restore_db, second["episodes"][0]["id"])

assert first_detail["episode"]["annotation_count"] == 1
assert len(first_detail["annotations"]) == 1
assert second_detail["episode"]["annotation_count"] == 1
assert len(second_detail["annotations"]) == 1
assert first_detail["annotations"][0]["annotation_id"] != second_detail["annotations"][0]["annotation_id"]

clear_local_task_history(restore_db, keep_task_id=second["task_id"])
scan_data_source(restore_db, source_b)
rescanned = episode_detail(restore_db, second["episodes"][0]["id"])
assert rescanned["episode"]["annotation_count"] == 1
assert len(rescanned["annotations"]) == 1
```

- [x] **Step 2: Run the collision test and verify RED**

Run:

```bash
UV_CACHE_DIR=/tmp/episode-qc-uv-cache uv run pytest tests/test_workspace_v1.py::test_v1_import_isolates_duplicate_annotation_ids_between_tasks -v
```

Expected: FAIL because importing `task_b` moves the original annotation out of `task_a`, leaving its stored count inconsistent with its annotation list. After the first fix attempt, it must still fail if deleting `task_a` makes the original ID free and rescanning `task_b` creates a duplicate.

- [x] **Step 3: Resolve conflicts to a deterministic Episode-scoped ID**

Add:

```python
def _resolve_import_annotation_id(
    connection: sqlite3.Connection,
    episode_id: str,
    source_episode_id: str,
    annotation_id: str,
) -> str:
    candidate_id = (
        annotation_id
        if source_episode_id == episode_id
        else _stable_id("ann", episode_id, annotation_id)
    )
    existing = connection.execute(
        "SELECT episode_id FROM annotation WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if existing is not None and str(existing["episode_id"]) != episode_id:
        raise ValueError(f"标注 ID 冲突: {annotation_id}")
    return candidate_id
```

Call it after generating/finding the requested ID and before the insert, passing `str(item.get("episode_id") or "")` as `source_episode_id`. Remove `episode_id = excluded.episode_id` from the upsert update list so an existing row cannot be reassigned as a side effect.

- [x] **Step 4: Run both focused tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/tmp/episode-qc-uv-cache uv run pytest tests/test_workspace_v1.py -k "restores_existing_annotations_json or isolates_duplicate_annotation_ids_between_tasks" -v
```

Expected: 2 passed.

---

### Task 3: Regression verification

**Files:**
- Verify: `src/episode_qc/workspace.py`
- Verify: `tests/test_workspace_v1.py`

**Interfaces:**
- Consumes: Tasks 1 and 2 behavior
- Produces: verification evidence only

- [x] **Step 1: Run workspace tests**

```bash
UV_CACHE_DIR=/tmp/episode-qc-uv-cache uv run pytest tests/test_workspace_v1.py
```

Expected: all tests pass.

- [x] **Step 2: Run the import-related Web test**

```bash
UV_CACHE_DIR=/tmp/episode-qc-uv-cache uv run pytest tests/test_web_server.py -k "import or rescan"
```

Expected: all selected tests pass.

- [x] **Step 3: Run the full suite**

```bash
UV_CACHE_DIR=/tmp/episode-qc-uv-cache uv run pytest
```

Expected: all tests pass with no failures or errors.

- [x] **Step 4: Check the final diff**

```bash
git diff --check
git status --short
git diff -- src/episode_qc/workspace.py tests/test_workspace_v1.py
```

Expected: no whitespace errors; only the approved implementation and pre-existing feature changes appear in the two code files.
