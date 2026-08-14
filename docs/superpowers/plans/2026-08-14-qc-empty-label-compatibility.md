# QC Empty-Label Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Flow and Episode-QC agree that a job whose complete frozen-label reference is empty is an unlabeled legacy job, while preserving strict rejection of partial or invalid references.

**Architecture:** Flow normalizes the no-label database state at its serializer boundary by publishing four JSON nulls. Episode-QC independently accepts both that canonical response and the currently deployed legacy response whose hash is an empty string; complete non-empty references continue through all existing integrity checks.

**Tech Stack:** Python 3.12, Django REST Framework, pytest, SQLite test databases

## Global Constraints

- Do not auto-create a task label set.
- Do not mutate existing `QualityControlJob.label_set_version` or `label_schema_hash` database rows.
- All four empty reference fields mean unlabeled mode; any partially populated reference remains an error.
- Unlabeled mode does not permit annotations without a complete Flow label reference.
- Do not modify or delete `.workspace`, failed cache state, NAS data, or 10.1.10.159 code.
- Keep Flow and QC changes in separate repository commits.

---

## File Structure

- `Episode-QC/tests/test_workspace_v1.py`: regression for the real deployed no-label Flow payload and existing partial-reference protection.
- `Episode-QC/src/episode_qc/workspace.py`: wire-value normalization before installing a frozen label schema.
- `Episode-Flow/operations/tests/test_quality_facts.py`: serializer regression for a QC job without a frozen label version.
- `Episode-Flow/operations/serializers.py`: canonical `null` hash for a job without a frozen label version.

### Task 1: Accept the deployed empty-label response in Episode-QC

**Files:**
- Modify: `tests/test_workspace_v1.py:218`
- Modify: `src/episode_qc/workspace.py:1839`

**Interfaces:**
- Consumes: `install_flow_label_schema(db_path: str | Path, job: dict[str, object]) -> dict[str, object]`
- Produces: `{"active": False}` for both an omitted reference and `null/null/""/null`; existing full-reference return values are unchanged.

- [ ] **Step 1: Add the failing real-payload regression**

Add immediately after `test_flow_label_schema_ignores_legacy_job_without_creating_workspace`:

```python
def test_flow_label_schema_treats_empty_flow_reference_as_unlabeled(tmp_path: Path):
    """Catches Flow's empty hash being mistaken for a partial label snapshot."""
    db_path = tmp_path / "workspace.db"
    flow_job = {
        "label_set_id": None,
        "label_schema_version": None,
        "label_schema_hash": "",
        "label_schema": None,
    }

    assert install_flow_label_schema(db_path, flow_job) == {"active": False}
    assert not db_path.exists()
```

- [ ] **Step 2: Run the regression and verify RED**

Run:

```bash
uv run pytest tests/test_workspace_v1.py::test_flow_label_schema_treats_empty_flow_reference_as_unlabeled -q
```

Expected: FAIL with `ValueError: Flow 冻结标签引用不完整`.

- [ ] **Step 3: Normalize only empty string reference fields**

Replace the `provided` construction in `install_flow_label_schema` with:

```python
    provided = {
        field: job.get(field)
        for field in reference_fields
        if (
            job.get(field) is not None
            and (field == "label_schema" or job.get(field) != "")
        )
    }
```

This treats an empty scalar ID/version/hash as absent, but an empty-string Schema as a supplied malformed value that still fails validation.

- [ ] **Step 4: Verify GREEN and strict-reference regressions**

Run:

```bash
uv run pytest \
  tests/test_workspace_v1.py::test_flow_label_schema_treats_empty_flow_reference_as_unlabeled \
  tests/test_workspace_v1.py::test_flow_label_schema_rejects_each_partial_reference_without_creating_workspace \
  tests/test_workspace_v1.py::test_flow_label_schema_rejects_bad_declared_hash_without_creating_workspace \
  tests/test_workspace_v1.py::test_flow_label_schema_installs_exact_snapshot_and_accepts_its_label \
  -q
```

Expected: 7 passed: one no-label case, four parametrized missing-field cases, one bad-hash case, and one valid snapshot case.

- [ ] **Step 5: Commit Episode-QC compatibility**

```bash
git add tests/test_workspace_v1.py src/episode_qc/workspace.py
git commit -m "fix: accept unlabeled Flow cache jobs"
```

### Task 2: Canonicalize Flow's no-label API response

**Files:**
- Modify: `/home/zw/workspace/Episode-Flow/operations/tests/test_quality_facts.py:261`
- Modify: `/home/zw/workspace/Episode-Flow/operations/serializers.py:783`

**Interfaces:**
- Consumes: `QualityControlJobSerializer(job).data`
- Produces: `label_set_id`, `label_schema_version`, `label_schema_hash`, and `label_schema` are all JSON null when `job.label_set_version_id` is null.

- [ ] **Step 1: Add the failing serializer contract test**

Add to `QualityFactSubmissionValidationTests` after `setUp`:

```python
    def test_unlabeled_job_serializes_a_consistently_empty_reference(self):
        """Catches an empty hash turning an otherwise absent reference partial."""
        payload = QualityControlJobSerializer(self.job).data

        self.assertIsNone(payload["label_set_id"])
        self.assertIsNone(payload["label_schema_version"])
        self.assertIsNone(payload["label_schema_hash"])
        self.assertIsNone(payload["label_schema"])
```

- [ ] **Step 2: Run the serializer regression and verify RED**

Run:

```bash
DJANGO_DB_BACKEND=sqlite uv run python manage.py test \
  operations.tests.test_quality_facts.QualityFactSubmissionValidationTests.test_unlabeled_job_serializes_a_consistently_empty_reference \
  -v 2
```

Expected: FAIL because `payload["label_schema_hash"]` is `""`, not `None`.

- [ ] **Step 3: Return a canonical null at the Flow boundary**

Change the no-label branch in `QualityControlJobSerializer.get_label_schema_hash`:

```python
        if not job.label_set_version_id:
            return None
```

Do not change the model field or the canonical hash calculation for labeled jobs.

- [ ] **Step 4: Verify GREEN and labeled snapshot behavior**

Run:

```bash
DJANGO_DB_BACKEND=sqlite uv run python manage.py test \
  operations.tests.test_quality_facts.QualityFactSubmissionValidationTests.test_unlabeled_job_serializes_a_consistently_empty_reference \
  operations.tests.test_quality_facts.QualityFactSubmissionValidationTests.test_ensure_qc_job_snapshots_the_active_task_label_set \
  -v 2
```

Expected: 2 tests pass; the labeled job still publishes its canonical 64-character digest.

- [ ] **Step 5: Commit Episode-Flow contract fix**

```bash
git add operations/tests/test_quality_facts.py operations/serializers.py
git commit -m "fix: serialize empty QC label references consistently"
```

### Task 3: Run cross-repository regression verification

**Files:**
- No file changes.

**Interfaces:**
- Consumes: committed QC and Flow behavior from Tasks 1 and 2.
- Produces: fresh evidence that cache/workspace and Flow API/fact behavior remain compatible.

- [ ] **Step 1: Run Episode-QC platform regressions**

```bash
cd /home/zw/workspace/Episode-QC
uv run pytest tests/test_workspace_v1.py tests/test_platform_workflow.py tests/test_web_server.py -q
```

Expected: exit code 0 with no failed tests.

- [ ] **Step 2: Run Episode-Flow API and fact regressions**

```bash
cd /home/zw/workspace/Episode-Flow
DJANGO_DB_BACKEND=sqlite uv run python manage.py test \
  operations.tests.test_quality_facts operations.tests.test_api \
  -v 1
```

Expected: exit code 0 with no failed tests.

- [ ] **Step 3: Confirm only intended code and tests changed**

```bash
git -C /home/zw/workspace/Episode-QC status --short
git -C /home/zw/workspace/Episode-Flow status --short
git -C /home/zw/workspace/Episode-QC show --stat --oneline HEAD
git -C /home/zw/workspace/Episode-Flow show --stat --oneline HEAD
```

Expected: the commits contain only the files listed in Tasks 1 and 2; pre-existing unrelated untracked or modified files remain untouched.
