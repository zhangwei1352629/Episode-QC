from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import threading
from unittest.mock import Mock
import urllib.error

import pytest

import episode_qc.platform_workflow as platform_workflow
from episode_qc.platform_workflow import (
    FlowClient,
    FlowClientError,
    QualityCacheError,
    QualityCacheManager,
    canonical_json_sha256,
)


FLOW_SCHEMA = {
    "schema": {
        "schema_type": "annotation_label_schema",
        "schema_version": "1.0.0",
        "label_set_id": "task-quality",
        "label_set_name": "Task quality",
        "language": "en",
    },
    "severity_levels": [{"code": "warning", "name": "Warning", "order": 1}],
    "actions": [{"code": "review", "name": "Review"}],
    "groups": [{"code": "motion", "name": "Motion", "order": 1}],
    "labels": [
        {
            "code": "body_sway",
            "name": "Body sway",
            "group": "motion",
            "enabled": True,
            "annotation_scopes": ["time_range"],
            "target_types": ["body"],
            "fields": [],
        }
    ],
}


def test_windows_extended_path_preserves_target_and_adds_win32_prefix():
    unc = Path(r"\\nas.local\datasets\deep\asset")
    drive = Path(r"N:\deep\asset")

    assert str(platform_workflow._windows_extended_path(unc, windows=True)) == (
        r"\\?\UNC\nas.local\datasets\deep\asset"
    )
    assert str(platform_workflow._windows_extended_path(drive, windows=True)) == (
        r"\\?\N:\deep\asset"
    )
    assert platform_workflow._windows_extended_path(unc, windows=False) == unc


def test_cache_job_uses_extended_length_source_path(monkeypatch, tmp_path: Path):
    source = Path(r"\\nas.local\datasets\deep\asset")
    extended_source = Path(r"\\?\UNC\nas.local\datasets\deep\asset")
    observed = []
    manager = QualityCacheManager(tmp_path / "cache", reserve_bytes=0)

    monkeypatch.setattr(
        platform_workflow,
        "resolve_source_directory",
        lambda _value: source,
    )

    def use_extended_path(path):
        observed.append(path)
        return extended_source

    monkeypatch.setattr(platform_workflow, "_windows_extended_path", use_extended_path)

    def stop_after_source_resolution(_job, source_root):
        assert source_root == extended_source
        raise QualityCacheError("source path checked")

    monkeypatch.setattr(manager, "_manifest_file_specs", stop_after_source_resolution)

    with pytest.raises(QualityCacheError, match="source path checked"):
        manager.cache_job(
            object(),
            {
                "code": "QCJ-LONG-PATH",
                "status": "claimed",
                "label_schema": {},
                "source_uri": "/nas/deep/asset",
            },
        )

    assert observed == [source]


def test_flow_client_preserves_drf_list_error_message():
    client = FlowClient("http://flow.test")
    client.opener.open = Mock(
        side_effect=urllib.error.HTTPError(
            "http://flow.test/api/v1/qc/jobs/QCJ-001/work",
            400,
            "Bad Request",
            {},
            BytesIO("[\"质检员已有进行中的工作时段\"]".encode("utf-8")),
        )
    )

    with pytest.raises(FlowClientError, match="质检员已有进行中的工作时段"):
        client.request("POST", "/api/v1/qc/jobs/QCJ-001/work", {})


def test_flow_client_omits_null_optional_annotation_fields_from_submission():
    client = FlowClient("http://flow.test")
    client.request = Mock(return_value={"code": "QCJ-NULL", "status": "completed"})
    annotation = {
        "id": "ann-null",
        "label_code": "body_sway",
        "scope": "time_range",
        "start_offset_ns": 1,
        "end_offset_ns": 2,
        "target_type": "body",
        "target_key": None,
        "severity": None,
        "action": None,
        "comment": None,
        "attributes": None,
    }

    client.submit_result(
        "QCJ-NULL",
        episode_results=[
            {
                "episode_id": "AST-NULL-EP0001",
                "decision": "pass_with_labels",
                "annotation_count": 1,
                "annotations": [annotation],
            }
        ],
    )

    payload = client.request.call_args.args[2]
    submitted = payload["episode_results"][0]["annotations"][0]
    assert submitted == {
        "id": "ann-null",
        "label_code": "body_sway",
        "scope": "time_range",
        "start_offset_ns": 1,
        "end_offset_ns": 2,
        "target_type": "body",
    }
    assert annotation["target_key"] is None


def test_flow_client_submits_open_ego_annotations_without_a_label_set():
    client = FlowClient("http://flow.test")
    client.request = Mock(return_value={"code": "QCJ-EGO", "status": "completed"})

    client.submit_result(
        "QCJ-EGO",
        annotation_mode="open",
        annotation_schema_version="ego_open_v1",
        episode_results=[
            {
                "episode_id": "EGO-EP0001",
                "decision": "pass_with_labels",
                "annotation_count": 1,
                "annotations": [
                    {
                        "id": "ego-ann-1",
                        "annotation_type": "action",
                        "label_name": "双手拿起红色咖啡杯",
                        "label_slug": "custom_cup",
                        "scope": "time_range",
                        "start_offset_ns": 1,
                        "end_offset_ns": 2,
                        "target_type": "mocap",
                    }
                ],
            }
        ],
    )

    payload = client.request.call_args.args[2]
    assert payload["annotation_mode"] == "open"
    assert payload["annotation_schema_version"] == "ego_open_v1"
    assert "label_set" not in payload
    annotation = payload["episode_results"][0]["annotations"][0]
    assert annotation["label_name"] == "双手拿起红色咖啡杯"
    assert annotation["annotation_type"] == "action"


def test_flow_client_renews_claim_without_creating_work_heartbeat():
    client = FlowClient("http://flow.test")
    client.request = Mock(return_value={"code": "QCJ-HEARTBEAT", "status": "in_progress"})

    response = client.heartbeat("QCJ-HEARTBEAT")

    assert response == {"code": "QCJ-HEARTBEAT", "status": "in_progress"}
    client.request.assert_called_once_with(
        "POST", "/api/v1/qc/jobs/QCJ-HEARTBEAT/heartbeat", {}
    )


def test_flow_client_fetches_full_job_detail_for_previous_review():
    client = FlowClient("http://flow.test")
    client.request = Mock(return_value={"code": "QCJ-RECHECK", "episodes": []})

    response = client.job("QCJ-RECHECK")

    assert response == {"code": "QCJ-RECHECK", "episodes": []}
    client.request.assert_called_once_with("GET", "/api/v1/qc/jobs/QCJ-RECHECK")


def test_atomic_json_writer_uses_platform_independent_lf(tmp_path: Path):
    target = tmp_path / "result.json"
    QualityCacheManager._write_json_atomic(target, {"name": "测试"})

    assert target.read_bytes() == b'{\n  "name": "\xe6\xb5\x8b\xe8\xaf\x95"\n}\n'


def test_atomic_json_writer_retries_transient_windows_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / ".qc-cache.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    real_replace = platform_workflow.os.replace
    replacements = []

    def replace_with_transient_lock(source, destination):
        replacements.append((Path(source), Path(destination)))
        if len(replacements) == 1:
            raise PermissionError(13, "file is temporarily locked", str(destination))
        return real_replace(source, destination)

    sleep = Mock()
    monkeypatch.setattr(platform_workflow.os, "replace", replace_with_transient_lock)
    monkeypatch.setattr(platform_workflow.time, "sleep", sleep)

    QualityCacheManager._write_json_atomic(target, {"cache_complete": False})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "cache_complete": False
    }
    assert len(replacements) == 2
    assert replacements[0][0] == replacements[1][0]
    assert replacements[0][0].name.startswith("..qc-cache.json.")
    assert replacements[0][0].name.endswith(".partial")
    assert replacements[0][1] == target
    assert not replacements[0][0].exists()
    sleep.assert_called_once()


def test_concurrent_atomic_json_writers_use_independent_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / ".qc-cache.json"
    real_replace = platform_workflow.os.replace
    barrier = threading.Barrier(2)
    sources = []
    sources_lock = threading.Lock()

    def synchronized_replace(source, destination):
        with sources_lock:
            sources.append(Path(source))
        barrier.wait(timeout=3)
        return real_replace(source, destination)

    monkeypatch.setattr(platform_workflow.os, "replace", synchronized_replace)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                QualityCacheManager._write_json_atomic, target, {"writer": writer}
            )
            for writer in (1, 2)
        ]
        for future in futures:
            future.result()

    assert len(set(sources)) == 2
    assert json.loads(target.read_text(encoding="utf-8"))["writer"] in {1, 2}
    assert list(tmp_path.glob("*.partial")) == []


def test_result_job_root_cannot_escape_assigned_asset_and_job():
    with pytest.raises(QualityCacheError, match="当前资产和任务编号"):
        QualityCacheManager._validate_result_job_root(
            r"C:\nas\data_collection\AST-001",
            asset_id="AST-001",
            job_code="QCJ-001",
            field_name="质检结果上传目录",
        )


def test_existing_published_result_is_rehashed_before_idempotent_reuse(tmp_path: Path):
    destination = tmp_path / "attempt-0001"
    destination.mkdir()
    result_path = destination / "qc_result.json"
    result_path.write_bytes(b"tampered")
    expected_sha256 = hashlib.sha256(b"expected").hexdigest()
    manifest = {
        "schema_version": 1,
        "result_id": "QCR-001",
        "result_sha256": expected_sha256,
        "job_code": "QCJ-001",
        "asset_id": "AST-001",
        "attempt": 1,
        "source_manifest_sha256": "a" * 64,
        "created_at": "2026-08-07T00:00:00+00:00",
        "files": [
            {
                "relative_path": "qc_result.json",
                "size_bytes": len(b"expected"),
                "sha256": expected_sha256,
            }
        ],
    }
    (destination / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(QualityCacheError, match="SHA-256"):
        QualityCacheManager._verify_published_result(destination, manifest)


def test_latest_result_copy_uses_the_actual_source_uri_when_asset_alias_differs(
    tmp_path: Path,
):
    """Catches the latest result being written beside an unreviewed asset alias."""
    source_root = tmp_path / "reviewed-source"
    alias_root = tmp_path / "asset-alias"
    source_root.mkdir()
    alias_root.mkdir()
    local_result = tmp_path / "qc_result.json"
    local_result.write_text('{"result_id": "QCR-SOURCE"}\n', encoding="utf-8")
    result_sha256 = hashlib.sha256(local_result.read_bytes()).hexdigest()

    published = QualityCacheManager._publish_latest_result_copy(
        {
            "source_uri": str(source_root),
            "asset_nas_uri": str(alias_root),
        },
        local_result,
        result_sha256=result_sha256,
    )

    assert published == str(source_root / "qc_result.json")
    assert (source_root / "qc_result.json").read_bytes() == local_result.read_bytes()
    assert not (alias_root / "qc_result.json").exists()


def test_versioned_result_copy_preserves_every_label_schema_version(tmp_path: Path):
    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    first_result = tmp_path / "v1.json"
    second_result = tmp_path / "v2.json"
    first_result.write_bytes(b'{"result_id":"QCR-V1"}\n')
    second_result.write_bytes(b'{"result_id":"QCR-V2"}\n')

    def manifest(path: Path, *, result_id: str, job_code: str) -> dict:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "schema_version": 1,
            "result_id": result_id,
            "result_sha256": digest,
            "job_code": job_code,
            "asset_id": "AST-HISTORY",
            "attempt": 1,
            "files": [
                {
                    "relative_path": "qc_result.json",
                    "size_bytes": path.stat().st_size,
                    "sha256": digest,
                }
            ],
        }

    first_manifest = manifest(
        first_result,
        result_id="QCR-V1",
        job_code="QCJ-HISTORY-V1",
    )
    second_manifest = manifest(
        second_result,
        result_id="QCR-V2",
        job_code="QCJ-HISTORY-V2",
    )

    first_path = QualityCacheManager._publish_versioned_result_copy(
        {"source_uri": str(asset_root), "code": "QCJ-HISTORY-V1"},
        first_result,
        first_manifest,
        label_set={
            "label_set_id": "labels-history",
            "label_schema_version": "1.0.0",
            "label_schema_hash": "1" * 64,
        },
    )
    QualityCacheManager._publish_latest_result_copy(
        {"source_uri": str(asset_root)},
        first_result,
        result_sha256=first_manifest["result_sha256"],
    )
    second_path = QualityCacheManager._publish_versioned_result_copy(
        {"source_uri": str(asset_root), "code": "QCJ-HISTORY-V2"},
        second_result,
        second_manifest,
        label_set={
            "label_set_id": "labels-history",
            "label_schema_version": "2.0.0",
            "label_schema_hash": "2" * 64,
        },
    )
    QualityCacheManager._publish_latest_result_copy(
        {"source_uri": str(asset_root)},
        second_result,
        result_sha256=second_manifest["result_sha256"],
    )

    assert first_path == str(
        asset_root
        / "qc_results"
        / "v1.0.0"
        / "QCJ-HISTORY-V1"
        / "attempt-0001"
        / "qc_result.json"
    )
    assert second_path == str(
        asset_root
        / "qc_results"
        / "v2.0.0"
        / "QCJ-HISTORY-V2"
        / "attempt-0001"
        / "qc_result.json"
    )
    assert Path(first_path).read_bytes() == first_result.read_bytes()
    assert Path(second_path).read_bytes() == second_result.read_bytes()
    assert (asset_root / "qc_result.json").read_bytes() == second_result.read_bytes()
    history_manifest = json.loads(
        (Path(first_path).parent / "result_manifest.json").read_text(encoding="utf-8")
    )
    assert history_manifest["storage_layout_version"] == 1
    assert history_manifest["label_set"]["label_schema_version"] == "1.0.0"


def test_open_annotation_history_uses_the_annotation_schema_version(tmp_path: Path):
    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    local_result = tmp_path / "open-result.json"
    local_result.write_bytes(b'{"result_id":"QCR-OPEN"}\n')
    digest = hashlib.sha256(local_result.read_bytes()).hexdigest()
    result_manifest = {
        "schema_version": 1,
        "result_id": "QCR-OPEN",
        "result_sha256": digest,
        "job_code": "QCJ-OPEN",
        "asset_id": "AST-OPEN",
        "attempt": 1,
        "files": [
            {
                "relative_path": "qc_result.json",
                "size_bytes": local_result.stat().st_size,
                "sha256": digest,
            }
        ],
    }

    published = QualityCacheManager._publish_versioned_result_copy(
        {
            "source_uri": str(asset_root),
            "code": "QCJ-OPEN",
            "annotation_mode": "open",
            "annotation_schema_version": "ego_open_v1",
        },
        local_result,
        result_manifest,
        label_set=None,
    )

    assert published == str(
        asset_root
        / "qc_results"
        / "vego_open_v1"
        / "QCJ-OPEN"
        / "attempt-0001"
        / "qc_result.json"
    )


def test_partitioned_initial_results_publish_one_complete_asset_aggregate(
    tmp_path: Path,
):
    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    asset_id = "AST-PARTITIONED"

    def publish_partition(job_code: str, partition_index: int, episode_id: str) -> dict:
        result_document = {
            "schema_version": 2,
            "result_id": f"QCR-{partition_index}",
            "job_code": job_code,
            "asset_id": asset_id,
            "attempt": 1,
            "source_manifest_sha256": "a" * 64,
            "annotation_mode": "open",
            "annotation_schema_version": "ego_open_v1",
            "episode_results": [
                {
                    "episode_id": episode_id,
                    "decision": "pass",
                    "annotation_count": 0,
                    "annotations": [],
                }
            ],
            "result": {},
        }
        destination = (
            tmp_path
            / "qc-results"
            / asset_id
            / job_code
            / "attempt-0001"
        )
        destination.mkdir(parents=True)
        result_path = destination / "qc_result.json"
        result_path.write_text(
            json.dumps(result_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "job_code": job_code,
            "partition_index": partition_index,
            "status": "completed",
            "reviewer_employee_no": f"QC-{partition_index}",
            "reviewer_name": f"Reviewer {partition_index}",
            "result_id": result_document["result_id"],
            "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
            "result_nas_path": str(result_path),
            "completed_at": f"2026-09-01T0{partition_index}:00:00+00:00",
            "episode_ids": [episode_id],
        }

    first = publish_partition("QCJ-PART-001", 1, "EP-001")
    second = publish_partition("QCJ-PART-002", 2, "EP-002")
    job = {
        "code": second["job_code"],
        "job_type": "initial",
        "affects_current_result": True,
        "asset_id": asset_id,
        "source_uri": str(asset_root),
        "asset_episode_count": 2,
        "asset_initial_job_count": 2,
        "asset_manifest_sha256": "a" * 64,
        "asset_manifest": {
            "asset_id": asset_id,
            "episodes": [
                {"episode_id": "EP-001", "sequence_index": 1},
                {"episode_id": "EP-002", "sequence_index": 2},
            ],
        },
        "annotation_mode": "open",
        "annotation_schema_version": "ego_open_v1",
        "asset_initial_partitions": [
            first,
            {**second, "status": "in_progress", "result_id": ""},
        ],
    }

    pending = QualityCacheManager._publish_asset_aggregate_if_complete(job)

    assert pending is None
    assert not (asset_root / "qc_result.json").exists()

    job["asset_initial_partitions"] = [first, second]
    published = QualityCacheManager._publish_asset_aggregate_if_complete(job)

    assert published == str(asset_root / "qc_result.json")
    aggregate = json.loads((asset_root / "qc_result.json").read_text(encoding="utf-8"))
    assert aggregate["result_type"] == "asset_aggregate"
    assert aggregate["complete"] is True
    assert aggregate["result_id"].startswith("QCA-")
    assert [row["episode_id"] for row in aggregate["episode_results"]] == [
        "EP-001",
        "EP-002",
    ]
    assert [row["job_code"] for row in aggregate["partition_results"]] == [
        "QCJ-PART-001",
        "QCJ-PART-002",
    ]
    assert aggregate["result"]["reviewed_episode_count"] == 2
    assert aggregate["result"]["partition_count"] == 2


def test_partition_submit_does_not_publish_a_partial_asset_root_result(
    tmp_path: Path,
    monkeypatch,
):
    cache = QualityCacheManager(tmp_path / "cache", reserve_bytes=0)
    job = {
        "code": "QCJ-PARTIAL-001",
        "job_type": "initial",
        "affects_current_result": True,
        "asset_id": "AST-PARTIAL",
        "asset_initial_job_count": 2,
        "asset_episode_count": 2,
        "status": "in_progress",
        "annotation_mode": "open",
        "annotation_schema_version": "ego_open_v1",
        "episodes": [
            {
                "episode_id": "EP-001",
                "relative_path": "episode_000001",
            }
        ],
    }
    state_path = cache.cache_root / "ready" / job["code"] / ".qc-cache.json"
    state_path.parent.mkdir(parents=True)
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "asset_id": job["asset_id"],
            "asset_manifest_sha256": "a" * 64,
            "cache_complete": True,
            "next_attempt": 1,
            "episodes": [
                {
                    "episode_id": "EP-001",
                    "relative_path": "episode_000001",
                    "status": "ready",
                }
            ],
        },
    )
    client = FakeFlowClient(job)
    publish_latest = Mock(side_effect=AssertionError("partial result reached asset root"))
    publish_aggregate = Mock(return_value=None)
    monkeypatch.setattr(cache, "_publish_result", Mock(return_value="/qc/part/qc_result.json"))
    monkeypatch.setattr(
        cache,
        "_publish_versioned_result_copy",
        Mock(return_value="/asset/qc_results/part/qc_result.json"),
    )
    monkeypatch.setattr(cache, "_publish_latest_result_copy", publish_latest)
    monkeypatch.setattr(
        cache,
        "_publish_asset_aggregate_if_complete",
        publish_aggregate,
    )
    monkeypatch.setattr(cache, "_verify_result_readback", Mock())
    monkeypatch.setattr(cache, "_verify_result_file_readback", Mock())

    response = cache.submit_result(
        client,
        job,
        episode_results=[
            {
                "episode_id": "EP-001",
                "decision": "pass",
                "annotation_count": 0,
                "annotations": [],
            }
        ],
    )

    assert response["status"] == "completed"
    publish_latest.assert_not_called()
    publish_aggregate.assert_called_once()


def test_versioned_result_copy_is_idempotent_and_rejects_conflicting_attempt(
    tmp_path: Path,
):
    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    local_result = tmp_path / "result.json"
    local_result.write_bytes(b'{"result_id":"QCR-STABLE"}\n')
    digest = hashlib.sha256(local_result.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "result_id": "QCR-STABLE",
        "result_sha256": digest,
        "job_code": "QCJ-STABLE",
        "asset_id": "AST-STABLE",
        "attempt": 1,
        "files": [
            {
                "relative_path": "qc_result.json",
                "size_bytes": local_result.stat().st_size,
                "sha256": digest,
            }
        ],
    }
    job = {"source_uri": str(asset_root), "code": "QCJ-STABLE"}
    label_set = {
        "label_set_id": "labels-stable",
        "label_schema_version": "2.0.0",
        "label_schema_hash": "3" * 64,
    }

    first = QualityCacheManager._publish_versioned_result_copy(
        job,
        local_result,
        manifest,
        label_set=label_set,
    )
    assert QualityCacheManager._publish_versioned_result_copy(
        job,
        local_result,
        manifest,
        label_set=label_set,
    ) == first

    conflicting = dict(manifest)
    conflicting["result_id"] = "QCR-CONFLICT"
    with pytest.raises(QualityCacheError, match="清单不同，禁止覆盖"):
        QualityCacheManager._publish_versioned_result_copy(
            job,
            local_result,
            conflicting,
            label_set=label_set,
        )


def test_versioned_result_copy_uses_short_windows_safe_staging_component(
    tmp_path: Path,
    monkeypatch,
):
    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    local_result = tmp_path / "result.json"
    local_result.write_bytes(b'{"result_id":"QCR-SHORT"}\n')
    digest = hashlib.sha256(local_result.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "result_id": "QCR-SHORT",
        "result_sha256": digest,
        "job_code": "QCJ-SHORT",
        "asset_id": "AST-SHORT",
        "attempt": 1,
        "files": [
            {
                "relative_path": "qc_result.json",
                "size_bytes": local_result.stat().st_size,
                "sha256": digest,
            }
        ],
    }
    real_replace = platform_workflow.os.replace
    directory_sources = []

    def capture_replace(source, destination, *args, **kwargs):
        if Path(source).is_dir():
            directory_sources.append(Path(source).name)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        platform_workflow.uuid,
        "uuid4",
        lambda: type("FixedUuid", (), {"hex": "1" * 32})(),
    )
    monkeypatch.setattr(platform_workflow.os, "replace", capture_replace)

    QualityCacheManager._publish_versioned_result_copy(
        {"source_uri": str(asset_root), "code": "QCJ-SHORT"},
        local_result,
        manifest,
        label_set={
            "label_set_id": "labels-short",
            "label_schema_version": "2.0.0",
            "label_schema_hash": "4" * 64,
        },
    )

    assert directory_sources == [".qcr-111111111111"]


def test_concurrent_latest_result_copies_use_independent_partial_files(
    tmp_path: Path, monkeypatch
):
    """Catches parallel QC jobs corrupting or deleting each other's partial copy."""
    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    first_result = tmp_path / "first.json"
    second_result = tmp_path / "second.json"
    first_result.write_bytes(b'{"result_id":"QCR-FIRST"}\n')
    second_result.write_bytes(b'{"result_id":"QCR-SECOND"}\n')
    expected_payloads = {first_result.read_bytes(), second_result.read_bytes()}
    first_replaced = threading.Event()
    second_replaced = threading.Event()
    real_replace = platform_workflow.os.replace

    def ordered_replace(source, destination, *args, **kwargs):
        payload = Path(source).read_bytes()
        if payload == first_result.read_bytes():
            replaced = real_replace(source, destination, *args, **kwargs)
            first_replaced.set()
            assert second_replaced.wait(timeout=5)
            return replaced
        assert first_replaced.wait(timeout=5)
        replaced = real_replace(source, destination, *args, **kwargs)
        second_replaced.set()
        return replaced

    monkeypatch.setattr(platform_workflow.os, "replace", ordered_replace)
    job = {"source_uri": str(asset_root)}

    def publish(local_result: Path) -> str:
        return QualityCacheManager._publish_latest_result_copy(
            job,
            local_result,
            result_sha256=hashlib.sha256(local_result.read_bytes()).hexdigest(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, (first_result, second_result)))

    assert results == [str(asset_root / "qc_result.json")] * 2
    assert (asset_root / "qc_result.json").read_bytes() in expected_payloads
    assert list(asset_root.glob("*.partial")) == []


def publish_asset_manifest(asset_root: Path, job: dict, relative_files: list[str]) -> dict:
    episode_manifests = {}
    for relative_text in relative_files:
        relative = Path(relative_text)
        source = asset_root / relative
        episode_directory = relative.parent.as_posix()
        episode_manifests.setdefault(episode_directory, []).append(
            {
                "relative_path": relative.as_posix(),
                "size_bytes": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        )
    manifest_episodes = []
    for episode in job["episodes"]:
        copied = dict(episode)
        copied["manifest"] = {
            "schema_version": 1,
            "files": episode_manifests.get(episode["relative_path"], []),
        }
        manifest_episodes.append(copied)
    manifest = {
        "schema_version": 1,
        "asset_id": job["asset_id"],
        "episodes": manifest_episodes,
    }
    (asset_root / "asset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    job["asset_manifest"] = manifest
    job["asset_manifest_sha256"] = canonical_json_sha256(manifest)
    job["result_upload_uri"] = str(
        asset_root.parent / "qc-results" / job["asset_id"] / job["code"]
    )
    job["result_staging_uri"] = str(
        asset_root.parent / "incoming" / "qc-results" / job["asset_id"] / job["code"]
    )
    job["next_attempt"] = 1
    return manifest


class FakeFlowClient:
    def __init__(self, job):
        self.job = job
        self.cache_reports = []
        self.work_reports = []
        self.results = []

    def claim(self, job_code):
        assert job_code == self.job["code"]
        return self.job

    def report_cache(self, job_code, **values):
        assert job_code == self.job["code"]
        self.cache_reports.append(values)
        return {**self.job, **values}

    def report_work(self, job_code, *, action, **values):
        assert job_code == self.job["code"]
        self.work_reports.append({"action": action, **values})
        return {**self.job, "status": "in_progress", "action": action, **values}

    def submit_result(self, job_code, **values):
        assert job_code == self.job["code"]
        self.results.append(values)
        return {**self.job, "status": "completed", "submitted": values}


def test_cache_job_materializes_flow_manifest_for_ego_without_writing_source(
    tmp_path: Path,
):
    asset_root = tmp_path / "external-ego" / "AST-EGO"
    episode_root = asset_root / "episode_000001"
    episode_root.mkdir(parents=True)
    primary = episode_root / "capture.mcap"
    primary.write_bytes(b"ego-mcap-payload")
    job = {
        "code": "QCJ-EGO",
        "asset_id": "AST-EGO",
        "asset_type": "egocentric",
        "viewer_profile": "ego_omniego",
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-EGO-EP0001",
                "relative_path": "episode_000001",
                "primary_file": "capture.mcap",
                "checksum_sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
            }
        ],
    }
    manifest = publish_asset_manifest(
        asset_root,
        job,
        ["episode_000001/capture.mcap"],
    )
    source_manifest = asset_root / "asset_manifest.json"
    source_manifest.unlink()

    cached = QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
        FakeFlowClient(job),
        job,
    )

    cached_manifest = Path(cached["cache_dir"]) / "asset_manifest.json"
    assert json.loads(cached_manifest.read_text(encoding="utf-8")) == manifest
    assert canonical_json_sha256(json.loads(cached_manifest.read_text(encoding="utf-8"))) == (
        job["asset_manifest_sha256"]
    )
    assert source_manifest.exists() is False
    assert (Path(cached["cache_dir"]) / "episode_000001/capture.mcap").read_bytes() == (
        primary.read_bytes()
    )


def test_cache_job_still_requires_published_manifest_for_non_ego_job(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-ROBOT"
    episode_root = asset_root / "episode_000001"
    episode_root.mkdir(parents=True)
    primary = episode_root / "motion.bvh"
    primary.write_bytes(b"robot-motion")
    job = {
        "code": "QCJ-ROBOT",
        "asset_id": "AST-ROBOT",
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-ROBOT-EP0001",
                "relative_path": "episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
            }
        ],
    }
    publish_asset_manifest(
        asset_root,
        job,
        ["episode_000001/motion.bvh"],
    )
    (asset_root / "asset_manifest.json").unlink()

    with pytest.raises(QualityCacheError, match="尚未发布 asset_manifest.json"):
        QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
            FakeFlowClient(job),
            job,
        )


def test_submit_result_binds_gapped_episode_directories_instead_of_list_positions(
    tmp_path: Path,
):
    asset_root = tmp_path / "nas" / "AST-GAPPED"
    episode_20 = asset_root / "episodes" / "episode_000020"
    episode_22 = asset_root / "episodes" / "episode_000022"
    episode_20.mkdir(parents=True)
    episode_22.mkdir(parents=True)
    primary_20 = episode_20 / "motion.bvh"
    primary_22 = episode_22 / "motion.bvh"
    primary_20.write_bytes(b"episode twenty")
    primary_22.write_bytes(b"episode twenty-two")
    job = {
        "code": "QCJ-GAPPED",
        "version": 1,
        "asset_id": "AST-GAPPED",
        "asset_size_bytes": 0,
        "asset_nas_uri": str(asset_root),
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-GAPPED-EP0001",
                "relative_path": "episodes/episode_000020",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(primary_20.read_bytes()).hexdigest(),
            },
            {
                "episode_id": "AST-GAPPED-EP0002",
                "relative_path": "episodes/episode_000022",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(primary_22.read_bytes()).hexdigest(),
            },
        ],
    }
    publish_asset_manifest(
        asset_root,
        job,
        [
            "episodes/episode_000020/motion.bvh",
            "episodes/episode_000022/motion.bvh",
        ],
    )
    job["asset_size_bytes"] = sum(
        path.stat().st_size for path in asset_root.rglob("*") if path.is_file()
    )
    client = FakeFlowClient(job)
    cache = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    cache.cache_job(client, job)

    with pytest.raises(
        QualityCacheError,
        match="标注结果与实际数据目录不一致",
    ):
        cache.submit_result(
            client,
            job,
            episode_results=[
                {
                    "episode_id": "AST-GAPPED-EP0001",
                    "relative_episode_path": "episodes/episode_000022",
                    "decision": "pass",
                    "annotation_count": 0,
                },
                {
                    "episode_id": "AST-GAPPED-EP0002",
                    "relative_episode_path": "episodes/episode_000020",
                    "decision": "discard",
                    "annotation_count": 0,
                },
            ],
        )

    cache.submit_result(
        client,
        job,
        episode_results=[
            {
                "episode_id": "AST-GAPPED-EP0002",
                "decision": "discard",
                "annotation_count": 0,
            },
            {
                "episode_id": "AST-GAPPED-EP0001",
                "decision": "pass",
                "annotation_count": 0,
            },
        ],
    )

    result = json.loads((asset_root / "qc_result.json").read_text(encoding="utf-8"))
    assert [item["episode_name"] for item in result["episode_results"]] == [
        "episode_000020",
        "episode_000022",
    ]
    results_by_id = {item["episode_id"]: item for item in result["episode_results"]}
    assert results_by_id["AST-GAPPED-EP0001"]["relative_episode_path"] == (
        "episodes/episode_000020"
    )
    assert results_by_id["AST-GAPPED-EP0001"]["episode_name"] == "episode_000020"
    assert results_by_id["AST-GAPPED-EP0001"]["result"][
        "relative_episode_path"
    ] == "episodes/episode_000020"
    assert results_by_id["AST-GAPPED-EP0002"]["relative_episode_path"] == (
        "episodes/episode_000022"
    )
    assert results_by_id["AST-GAPPED-EP0002"]["episode_name"] == "episode_000022"


def test_cache_job_verifies_each_cached_file_once_and_reports_file_progress(
    tmp_path: Path, monkeypatch
):
    asset_root = tmp_path / "nas" / "AST-SINGLE-PASS"
    first_episode = asset_root / "episodes" / "episode_000001"
    second_episode = asset_root / "episodes" / "episode_000002"
    first_episode.mkdir(parents=True)
    second_episode.mkdir(parents=True)
    first_primary = first_episode / "motion.bvh"
    second_primary = second_episode / "motion.bvh"
    first_primary.write_bytes(b"first primary payload")
    second_primary.write_bytes(b"second primary payload")
    first_checksum = hashlib.sha256(first_primary.read_bytes()).hexdigest()
    second_checksum = hashlib.sha256(second_primary.read_bytes()).hexdigest()
    job = {
        "code": "QCJ-SINGLE-PASS",
        "asset_id": "AST-SINGLE-PASS",
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-SINGLE-PASS-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": first_checksum,
            },
            {
                "episode_id": "AST-SINGLE-PASS-EP0002",
                "relative_path": "episodes/episode_000002",
                "primary_file": "motion.bvh",
                "checksum_sha256": second_checksum,
            },
        ],
    }
    publish_asset_manifest(
        asset_root,
        job,
        [
            "episodes/episode_000001/motion.bvh",
            "episodes/episode_000002/motion.bvh",
        ],
    )
    cache_root = tmp_path / "qc-cache"
    manager = QualityCacheManager(cache_root, reserve_bytes=0, chunk_size=7)
    original_sha256_file = platform_workflow.sha256_file
    cache_hash_calls: dict[str, int] = {}
    downloading_root = cache_root / "downloading"
    ready_root = cache_root / "ready" / "QCJ-SINGLE-PASS" / "AST-SINGLE-PASS"

    def count_cache_hashes(path: str | Path) -> str:
        path = Path(path)
        if path.is_relative_to(downloading_root):
            relative = path.relative_to(
                downloading_root / "QCJ-SINGLE-PASS.partial" / "AST-SINGLE-PASS"
            ).as_posix()
            cache_hash_calls[relative] = cache_hash_calls.get(relative, 0) + 1
        elif path.is_relative_to(ready_root):
            relative = path.relative_to(ready_root).as_posix()
            cache_hash_calls[relative] = cache_hash_calls.get(relative, 0) + 1
        return original_sha256_file(path)

    monkeypatch.setattr(platform_workflow, "sha256_file", count_cache_hashes)
    progress: list[dict] = []

    manager.cache_job(FakeFlowClient(job), job, progress_callback=progress.append)

    assert cache_hash_calls == {
        "asset_manifest.json": 1,
        "episodes/episode_000001/motion.bvh": 1,
        "episodes/episode_000002/motion.bvh": 1,
    }
    verification = [item for item in progress if item.get("phase") == "verifying"]
    assert [item["verified_files"] for item in verification] == [1, 2, 3]
    assert [item["current_file"] for item in verification] == [
        "asset_manifest.json",
        "episodes/episode_000001/motion.bvh",
        "episodes/episode_000002/motion.bvh",
    ]
    assert all(item["total_files"] == 3 for item in verification)


def test_cache_job_publishes_each_episode_before_copying_the_next_one(
    tmp_path: Path, monkeypatch
):
    """A reviewer may start on Episode 1 while the worker downloads Episode 2."""
    asset_root = tmp_path / "nas" / "AST-PROGRESSIVE"
    first_source = asset_root / "episodes" / "episode_000001"
    second_source = asset_root / "episodes" / "episode_000002"
    first_source.mkdir(parents=True)
    second_source.mkdir(parents=True)
    first_primary = first_source / "motion.bvh"
    second_primary = second_source / "motion.bvh"
    first_primary.write_bytes(b"first Episode")
    second_primary.write_bytes(b"second Episode")
    job = {
        "code": "QCJ-PROGRESSIVE",
        "asset_id": "AST-PROGRESSIVE",
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-PROGRESSIVE-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(first_primary.read_bytes()).hexdigest(),
            },
            {
                "episode_id": "AST-PROGRESSIVE-EP0002",
                "relative_path": "episodes/episode_000002",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(second_primary.read_bytes()).hexdigest(),
            },
        ],
    }
    publish_asset_manifest(
        asset_root,
        job,
        [
            "episodes/episode_000001/motion.bvh",
            "episodes/episode_000002/motion.bvh",
        ],
    )
    manager = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0, chunk_size=3)
    episode_ready: list[dict] = []
    original_copy = manager._copy_resumable

    def observe_second_episode_copy(source, *args, **kwargs):
        if Path(source) == second_primary:
            assert [item["episode_id"] for item in episode_ready] == [
                "AST-PROGRESSIVE-EP0001"
            ]
            assert (
                tmp_path
                / "qc-cache"
                / "ready"
                / "QCJ-PROGRESSIVE"
                / "AST-PROGRESSIVE"
                / "episodes"
                / "episode_000001"
                / "motion.bvh"
            ).read_bytes() == b"first Episode"
            state = json.loads(
                (
                    tmp_path
                    / "qc-cache"
                    / "ready"
                    / "QCJ-PROGRESSIVE"
                    / ".qc-cache.json"
                ).read_text(encoding="utf-8")
            )
            assert state["cache_status"] == "partially_ready"
        return original_copy(source, *args, **kwargs)

    monkeypatch.setattr(manager, "_copy_resumable", observe_second_episode_copy)

    cached = manager.cache_job(
        FakeFlowClient(job),
        job,
        episode_ready_callback=episode_ready.append,
    )

    assert cached["cache_complete"] is True
    assert [item["episode_id"] for item in episode_ready] == [
        "AST-PROGRESSIVE-EP0001",
        "AST-PROGRESSIVE-EP0002",
    ]


def test_submit_result_rejects_a_job_whose_episode_cache_is_incomplete(tmp_path: Path):
    cache = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    job = {
        "code": "QCJ-INCOMPLETE",
        "asset_id": "AST-INCOMPLETE",
        "episodes": [],
    }
    state_path = tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json"
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "schema_version": 3,
            "job_code": job["code"],
            "asset_id": job["asset_id"],
            "cache_complete": False,
        },
    )

    with pytest.raises(QualityCacheError, match="尚未完整缓存"):
        cache.submit_result(FakeFlowClient(job), job, episode_results=[])


def test_submit_result_rejects_a_completed_flag_with_a_failed_episode(tmp_path: Path):
    cache = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    job = {
        "code": "QCJ-FAILED-EPISODE",
        "asset_id": "AST-FAILED-EPISODE",
        "episodes": [{"episode_id": "AST-FAILED-EPISODE-EP0001"}],
    }
    state_path = tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json"
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "schema_version": 3,
            "job_code": job["code"],
            "asset_id": job["asset_id"],
            "cache_complete": True,
            "episodes": [
                {
                    "episode_id": "AST-FAILED-EPISODE-EP0001",
                    "status": "failed",
                }
            ],
        },
    )

    with pytest.raises(QualityCacheError, match="尚未完整缓存"):
        cache.submit_result(FakeFlowClient(job), job, episode_results=[])


def test_cache_job_resumes_from_the_first_verified_episode_after_a_restart(
    tmp_path: Path, monkeypatch
):
    asset_root = tmp_path / "nas" / "AST-RESUME"
    first_source = asset_root / "episodes" / "episode_000001"
    second_source = asset_root / "episodes" / "episode_000002"
    first_source.mkdir(parents=True)
    second_source.mkdir(parents=True)
    first_primary = first_source / "motion.bvh"
    second_primary = second_source / "motion.bvh"
    first_primary.write_bytes(b"first retained Episode")
    second_primary.write_bytes(b"second resumable Episode")
    job = {
        "code": "QCJ-RESUME",
        "asset_id": "AST-RESUME",
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-RESUME-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(first_primary.read_bytes()).hexdigest(),
            },
            {
                "episode_id": "AST-RESUME-EP0002",
                "relative_path": "episodes/episode_000002",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(second_primary.read_bytes()).hexdigest(),
            },
        ],
    }
    publish_asset_manifest(
        asset_root,
        job,
        [
            "episodes/episode_000001/motion.bvh",
            "episodes/episode_000002/motion.bvh",
        ],
    )
    cache_root = tmp_path / "qc-cache"
    first_manager = QualityCacheManager(cache_root, reserve_bytes=0)
    original_copy = first_manager._copy_resumable

    def interrupt_second_episode(source, target, *args, **kwargs):
        if Path(source) == second_primary:
            partial = Path(target).with_name(Path(target).name + ".partial")
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(second_primary.read_bytes()[:7])
            raise QualityCacheError("simulated restart")
        return original_copy(source, target, *args, **kwargs)

    monkeypatch.setattr(first_manager, "_copy_resumable", interrupt_second_episode)
    with pytest.raises(QualityCacheError, match="simulated restart"):
        first_manager.cache_job(FakeFlowClient(job), job)

    state_path = cache_root / "ready" / job["code"] / ".qc-cache.json"
    interrupted = json.loads(state_path.read_text(encoding="utf-8"))
    assert interrupted["cache_complete"] is False
    assert [item["status"] for item in interrupted["episodes"]] == ["ready", "failed"]
    partial_path = (
        cache_root
        / "downloading"
        / f"{job['code']}.partial"
        / job["asset_id"]
        / "episodes"
        / "episode_000002"
        / "motion.bvh.partial"
    )
    assert partial_path.read_bytes() == second_primary.read_bytes()[:7]

    resumed_manager = QualityCacheManager(cache_root, reserve_bytes=0)
    resumed_sources: list[Path] = []
    resumed_copy = resumed_manager._copy_resumable

    def track_resumed_copy(source, *args, **kwargs):
        resumed_sources.append(Path(source))
        return resumed_copy(source, *args, **kwargs)

    monkeypatch.setattr(resumed_manager, "_copy_resumable", track_resumed_copy)
    resumed = resumed_manager.cache_job(FakeFlowClient(job), job)
    completed = json.loads(state_path.read_text(encoding="utf-8"))

    assert resumed["cache_complete"] is True
    assert completed["cache_status"] == "cache_ready"
    assert "cache_error" not in completed
    assert first_primary not in resumed_sources
    assert second_primary in resumed_sources
    assert not partial_path.exists()


def test_cache_job_recovers_an_episode_moved_before_its_state_was_saved(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-MOVED"
    episode_root = asset_root / "episodes" / "episode_000001"
    episode_root.mkdir(parents=True)
    primary = episode_root / "motion.bvh"
    primary.write_bytes(b"atomically moved Episode")
    job = {
        "code": "QCJ-MOVED",
        "asset_id": "AST-MOVED",
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-MOVED-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
            }
        ],
    }
    publish_asset_manifest(asset_root, job, ["episodes/episode_000001/motion.bvh"])
    manager = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    manager.cache_job(FakeFlowClient(job), job)

    state_path = tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json"
    interrupted = json.loads(state_path.read_text(encoding="utf-8"))
    interrupted["cache_complete"] = False
    interrupted["episodes"][0]["status"] = "caching"
    interrupted["episodes"][0]["primary_files"] = []
    QualityCacheManager._write_json_atomic(state_path, interrupted)

    resumed = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0).cache_job(
        FakeFlowClient(job), job
    )

    assert resumed["cache_complete"] is True


def test_cache_job_keeps_processing_later_episodes_after_one_episode_fails(
    tmp_path: Path, monkeypatch
):
    asset_root = tmp_path / "nas" / "AST-FAILURE"
    episode_ids = []
    relative_files = []
    episodes = []
    sources = []
    for index, payload in enumerate((b"first", b"broken", b"last"), start=1):
        relative_directory = f"episodes/episode_{index:06d}"
        source = asset_root / relative_directory / "motion.bvh"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        episode_id = f"AST-FAILURE-EP{index:04d}"
        episode_ids.append(episode_id)
        relative_files.append(f"{relative_directory}/motion.bvh")
        episodes.append(
            {
                "episode_id": episode_id,
                "relative_path": relative_directory,
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        sources.append(source)
    job = {
        "code": "QCJ-FAILURE",
        "asset_id": "AST-FAILURE",
        "source_uri": str(asset_root),
        "episodes": episodes,
    }
    publish_asset_manifest(asset_root, job, relative_files)
    manager = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    original_copy = manager._copy_resumable
    failed_copy_attempts = 0

    def fail_only_second_episode(source, *args, **kwargs):
        nonlocal failed_copy_attempts
        if Path(source) == sources[1]:
            failed_copy_attempts += 1
            raise QualityCacheError("simulated unreadable Episode")
        return original_copy(source, *args, **kwargs)

    monkeypatch.setattr(manager, "_copy_resumable", fail_only_second_episode)
    with pytest.raises(QualityCacheError, match="simulated unreadable Episode"):
        manager.cache_job(FakeFlowClient(job), job)

    state = json.loads(
        (tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["status"] for item in state["episodes"]] == ["ready", "failed", "ready"]
    assert failed_copy_attempts == 3
    assert "simulated unreadable Episode" in state["episodes"][1]["error"]
    assert (
        tmp_path
        / "qc-cache"
        / "ready"
        / job["code"]
        / job["asset_id"]
        / "episodes"
        / "episode_000003"
        / "motion.bvh"
    ).read_bytes() == b"last"


def test_cache_job_continues_when_initial_flow_cache_report_is_temporarily_unavailable(
    tmp_path: Path, monkeypatch
):
    asset_root = tmp_path / "nas" / "AST-FLOW-RETRY"
    episode_root = asset_root / "episodes" / "episode_000001"
    episode_root.mkdir(parents=True)
    primary = episode_root / "motion.bvh"
    primary.write_bytes(b"cache survives Flow outage")
    job = {
        "code": "QCJ-FLOW-RETRY",
        "asset_id": "AST-FLOW-RETRY",
        "source_uri": str(asset_root),
        "episodes": [{
            "episode_id": "AST-FLOW-RETRY-EP0001",
            "relative_path": "episodes/episode_000001",
            "primary_file": "motion.bvh",
            "checksum_sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
        }],
    }
    publish_asset_manifest(asset_root, job, ["episodes/episode_000001/motion.bvh"])

    class TransientFlowClient(FakeFlowClient):
        def __init__(self, value):
            super().__init__(value)
            self.failed_reports = 0

        def report_cache(self, job_code, **values):
            if self.failed_reports < 3:
                self.failed_reports += 1
                raise FlowClientError("temporary Flow outage")
            return super().report_cache(job_code, **values)

    monkeypatch.setattr(platform_workflow.time, "sleep", lambda _delay: None)
    client = TransientFlowClient(job)
    cached = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0).cache_job(client, job)

    assert cached["cache_complete"] is True
    assert client.failed_reports == 3
    assert client.cache_reports[-1]["status"] == "cache_ready"


def test_pending_final_flow_cache_report_is_retried_after_reconnect(tmp_path: Path, monkeypatch):
    cache = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    state_path = tmp_path / "qc-cache" / "ready" / "QCJ-PENDING-REPORT" / ".qc-cache.json"
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "schema_version": 3,
            "job_code": "QCJ-PENDING-REPORT",
            "asset_id": "AST-PENDING-REPORT",
            "cache_complete": True,
            "pending_cache_report": {
                "status": "cache_ready",
                "cache_progress": 100,
                "cached_bytes": 42,
                "cache_workstation": "QC-WS",
            },
        },
    )
    monkeypatch.setattr(platform_workflow.time, "sleep", lambda _delay: None)
    client = FakeFlowClient({"code": "QCJ-PENDING-REPORT"})

    assert cache.flush_pending_cache_report(client, "QCJ-PENDING-REPORT") is True
    assert client.cache_reports == [{
        "status": "cache_ready",
        "cache_progress": 100,
        "cached_bytes": 42,
        "cache_workstation": "QC-WS",
    }]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "pending_cache_report" not in state


def test_pre_cache_failure_report_is_durable_and_retried_after_reconnect(tmp_path: Path, monkeypatch):
    cache = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    state_path = (
        tmp_path / "qc-cache" / "failed" / "QCJ-PRE-CACHE-FAILURE" / ".qc-cache.json"
    )
    cache.record_pre_cache_failure(
        "QCJ-PRE-CACHE-FAILURE", "同一标签集版本已有不同的本地标签快照"
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["cache_status"] == "failed"
    assert state["cache_complete"] is False
    assert state["pending_cache_report"] == {
        "status": "failed",
        "cache_error": "同一标签集版本已有不同的本地标签快照",
    }
    assert cache.cache_summary("QCJ-PRE-CACHE-FAILURE") == {
        "cache_complete": False,
        "cache_status": "failed",
        "cache_error": "同一标签集版本已有不同的本地标签快照",
        "cached_episode_count": 0,
        "total_episode_count": 0,
        "cached_bytes": 0,
        "total_bytes": 0,
    }

    class UnavailableThenRecoveredClient:
        def __init__(self):
            self.available = False
            self.reports = []

        def report_cache(self, job_code, **values):
            self.reports.append((job_code, values))
            if not self.available:
                raise FlowClientError("temporary Flow outage")
            return {"code": job_code, **values}

    client = UnavailableThenRecoveredClient()
    monkeypatch.setattr(platform_workflow.time, "sleep", lambda _delay: None)

    assert cache.flush_pending_cache_report(client, "QCJ-PRE-CACHE-FAILURE") is False
    assert len(client.reports) == 3
    assert "pending_cache_report" in json.loads(state_path.read_text(encoding="utf-8"))

    client.available = True
    assert cache.flush_pending_cache_report(client, "QCJ-PRE-CACHE-FAILURE") is True
    assert client.reports[-1] == (
        "QCJ-PRE-CACHE-FAILURE",
        {
            "status": "failed",
            "cache_error": "同一标签集版本已有不同的本地标签快照",
        },
    )
    assert "pending_cache_report" not in json.loads(state_path.read_text(encoding="utf-8"))


def test_structured_label_flow_job_is_fully_cached_verified_submitted_and_safely_evicted(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-001"
    first_source = asset_root / "episodes" / "episode_000001"
    second_source = asset_root / "episodes" / "episode_000002"
    first_source.mkdir(parents=True)
    second_source.mkdir(parents=True)
    payload = (b"HIERARCHY\n" + b"x" * 1024) * 128
    second_payload = payload + b"second"
    primary = first_source / "motion.bvh"
    primary.write_bytes(payload)
    (second_source / "motion.bvh").write_bytes(second_payload)
    (first_source / "metadata.json").write_text('{"schema_version": 1}', encoding="utf-8")
    checksum = hashlib.sha256(payload).hexdigest()
    second_checksum = hashlib.sha256(second_payload).hexdigest()
    job = {
        "code": "QCJ-001",
        "version": 1,
        "asset_id": "AST-001",
        "asset_size_bytes": 0,
        "asset_nas_uri": str(asset_root),
        "source_uri": str(asset_root),
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
        "label_schema": FLOW_SCHEMA,
        "episodes": [
            {
                "episode_id": "AST-001-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": checksum,
            },
            {
                "episode_id": "AST-001-EP0002",
                "relative_path": "episodes/episode_000002",
                "primary_file": "motion.bvh",
                "checksum_sha256": second_checksum,
            },
        ],
    }
    publish_asset_manifest(
        asset_root,
        job,
        [
            "episodes/episode_000001/motion.bvh",
            "episodes/episode_000001/metadata.json",
            "episodes/episode_000002/motion.bvh",
        ],
    )
    total_bytes = sum(path.stat().st_size for path in asset_root.rglob("*") if path.is_file())
    job["asset_size_bytes"] = total_bytes
    client = FakeFlowClient(job)
    cache = QualityCacheManager(
        tmp_path / "qc-cache", reserve_bytes=0, workspace_name="QC-WS-TEST", chunk_size=4096
    )
    progress = []

    cached = cache.cache_job(client, job, progress_callback=progress.append)

    cached_primaries = [Path(path) for path in cached["primary_files"]]
    assert [path.read_bytes() for path in cached_primaries] == [payload, second_payload]
    assert cached["total_bytes"] == total_bytes
    assert client.cache_reports[0]["status"] == "caching"
    assert client.cache_reports[-1]["status"] == "cache_ready"
    assert client.cache_reports[-1]["cache_progress"] == 100
    assert progress[-1]["status"] == "cache_ready"
    legacy_state_path = tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json"
    legacy_state = json.loads(legacy_state_path.read_text(encoding="utf-8"))
    legacy_state["schema_version"] = 2
    for key in (
        "episodes",
        "asset_manifest_ready",
        "cache_complete",
        "cache_status",
        "cached_episode_count",
        "total_episode_count",
    ):
        legacy_state.pop(key, None)
    QualityCacheManager._write_json_atomic(legacy_state_path, legacy_state)
    reused = cache.cache_job(client, job)
    assert reused["reused"] is True
    assert reused["primary_files"] == cached["primary_files"]
    migrated_state = json.loads(legacy_state_path.read_text(encoding="utf-8"))
    assert migrated_state["schema_version"] == 3
    assert migrated_state["cache_complete"] is True
    assert [item["status"] for item in migrated_state["episodes"]] == ["ready", "ready"]

    cache.start_review(client, job["code"])
    assert client.work_reports == [{"action": "start", "workstation": "QC-WS-TEST"}]
    with pytest.raises(QualityCacheError, match="尚未同步"):
        cache.evict(job["code"])
    (asset_root / "qc_result.json").write_text('{"stale": true}\n', encoding="utf-8")

    submitted = cache.submit_result(
        client,
        job,
        episode_results=[
            {
                "episode_id": "AST-001-EP0001",
                "decision": "pass_with_labels",
                "quality_grade": "good",
                "annotation_count": 1,
                "annotations": [
                    {
                        "annotation_id": "ann-local-1",
                        "label_code": "body_sway",
                        "scope": "time_range",
                        "start_offset_ns": 100,
                        "end_offset_ns": 200,
                        "target_type": "body",
                        "target_key": "root",
                        "severity": "warning",
                        "action": "review",
                        "comment": "visible sway",
                        "attributes": {"axis": "x"},
                        "local_only": "must not publish",
                    }
                ],
                "result": {"labels": ["minor_jitter"], "annotations": [{"local": "context"}]},
            },
            {
                "episode_id": "AST-001-EP0002",
                "decision": "reject",
                "quality_grade": "invalid",
                "annotation_count": 0,
                "completed_at": "2026-08-10T09:12:00+00:00",
            },
        ],
        result={"reviewed_episode_count": 2},
        review_started_at="2026-08-10T09:00:00+00:00",
        review_completed_at="2026-08-10T09:12:00+00:00",
    )

    assert submitted["status"] == "completed"
    assert client.work_reports[-1] == {"action": "heartbeat"}
    result_path = (
        Path(job["result_upload_uri"])
        / "attempt-0001"
        / "qc_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    mirrored_result_path = asset_root / "qc_result.json"
    assert json.loads(mirrored_result_path.read_text(encoding="utf-8")) == result
    history_result_path = (
        asset_root
        / "qc_results"
        / "v1.0.0"
        / job["code"]
        / "attempt-0001"
        / "qc_result.json"
    )
    assert json.loads(history_result_path.read_text(encoding="utf-8")) == result
    history_manifest = json.loads(
        (history_result_path.parent / "result_manifest.json").read_text(encoding="utf-8")
    )
    assert history_manifest["label_set"] == {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }
    assert list(asset_root.glob("*.partial")) == []
    assert [item["decision"] for item in result["episode_results"]] == [
        "pass_with_labels",
        "discard",
    ]
    assert result["review_timing"] == {
        "source": "qc_database",
        "started_at": "2026-08-10T09:00:00+00:00",
        "completed_at": "2026-08-10T09:12:00+00:00",
        "duration_seconds": 720.0,
    }
    assert client.results[0]["result_nas_path"] == str(result_path)
    assert client.results[0]["result_id"].startswith("QCR-")
    assert client.results[0]["review_started_at"] == "2026-08-10T09:00:00+00:00"
    assert client.results[0]["review_completed_at"] == "2026-08-10T09:12:00+00:00"
    assert client.results[0]["episode_results"][1]["completed_at"] == (
        "2026-08-10T09:12:00+00:00"
    )
    assert client.results[0]["result_manifest"]["result_sha256"] == client.results[0][
        "result_sha256"
    ]
    assert client.results[0]["label_set"] == {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }
    assert client.results[0]["episode_results"][0]["annotations"] == [
        {
            "id": "ann-local-1",
            "label_code": "body_sway",
            "scope": "time_range",
            "start_offset_ns": 100,
            "end_offset_ns": 200,
            "target_type": "body",
            "target_key": "root",
            "severity": "warning",
            "action": "review",
            "comment": "visible sway",
            "attributes": {"axis": "x"},
        }
    ]
    assert client.results[0]["episode_results"][1]["annotations"] == []
    assert (result_path.parent / "result_manifest.json").is_file()
    assert not any(Path(job["result_staging_uri"]).glob("*.partial"))
    state = json.loads(
        (tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["result_synced"] is True
    assert datetime.fromisoformat(state["result_synced_at"]).tzinfo is not None
    assert state["result_readback_verified_at"] == state["result_synced_at"]

    cache.evict(job["code"])
    assert not (tmp_path / "qc-cache" / "ready" / job["code"]).exists()


def test_result_copy_failure_keeps_pending_result_and_does_not_complete_flow(
    tmp_path: Path, monkeypatch
):
    """Catches an unwritable asset mirror being mistaken for a completed QC job."""
    asset_root = tmp_path / "nas" / "AST-MIRROR-FAILURE"
    asset_root.mkdir(parents=True)
    job = {
        "code": "QCJ-MIRROR-FAILURE",
        "asset_id": "AST-MIRROR-FAILURE",
        "asset_nas_uri": str(asset_root),
        "source_uri": str(asset_root),
        "episodes": [{"episode_id": "AST-MIRROR-FAILURE-EP0001"}],
    }
    cache_root = tmp_path / "qc-cache"
    cache = QualityCacheManager(cache_root, reserve_bytes=0)
    state_path = cache_root / "ready" / job["code"] / ".qc-cache.json"
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "schema_version": 3,
            "job_code": job["code"],
            "asset_id": job["asset_id"],
            "cache_complete": True,
            "episodes": [
                {
                    "episode_id": "AST-MIRROR-FAILURE-EP0001",
                    "status": "ready",
                }
            ],
        },
    )
    monkeypatch.setattr(
        cache,
        "_publish_result",
        lambda *args: "/nas/qc-results/AST-MIRROR-FAILURE/QCJ-MIRROR-FAILURE/attempt-0001/qc_result.json",
    )

    def fail_copy(source, destination, *args, **kwargs):
        Path(destination).write_bytes(b"partial result")
        raise OSError("read-only asset directory")

    monkeypatch.setattr(platform_workflow.shutil, "copy2", fail_copy)
    client = FakeFlowClient(job)

    with pytest.raises(QualityCacheError, match="无法在原始数据目录保存版本化质检结果"):
        cache.submit_result(
            client,
            job,
            episode_results=[
                {
                    "episode_id": "AST-MIRROR-FAILURE-EP0001",
                    "decision": "pass",
                    "annotation_count": 0,
                }
            ],
        )

    assert client.results == []
    assert (cache_root / "results-pending" / job["code"] / "qc_result.json").is_file()
    assert list(asset_root.glob("*.partial")) == []
    assert list(asset_root.glob("qc_results/**/*.partial")) == []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pending_result"]["result_id"].startswith("QCR-")
    assert state.get("result_synced") is not True


def test_submit_requires_completed_flow_response_and_post_submit_nas_readback(
    tmp_path: Path,
):
    asset_root = tmp_path / "nas" / "AST-READBACK"
    asset_root.mkdir(parents=True)
    job = {
        "code": "QCJ-READBACK",
        "status": "in_progress",
        "asset_id": "AST-READBACK",
        "source_uri": str(asset_root),
        "result_upload_uri": str(
            tmp_path / "qc-results" / "AST-READBACK" / "QCJ-READBACK"
        ),
        "next_attempt": 1,
        "episodes": [{"episode_id": "AST-READBACK-EP0001"}],
    }
    cache_root = tmp_path / "qc-cache"
    cache = QualityCacheManager(cache_root, reserve_bytes=0)
    state_path = cache_root / "ready" / job["code"] / ".qc-cache.json"
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "schema_version": 3,
            "job_code": job["code"],
            "asset_id": job["asset_id"],
            "cache_complete": True,
            "episodes": [
                {"episode_id": "AST-READBACK-EP0001", "status": "ready"}
            ],
        },
    )

    class CorruptingClient:
        def report_work(self, *_args, **_kwargs):
            return None

        def submit_result(self, _job_code, **values):
            published = Path(values["result_nas_path"])
            published.write_text('{"corrupted": true}\n', encoding="utf-8")
            return {"status": "completed"}

    with pytest.raises(QualityCacheError, match="提交后 NAS 回读校验失败"):
        cache.submit_result(
            CorruptingClient(),
            job,
            episode_results=[
                {
                    "episode_id": "AST-READBACK-EP0001",
                    "decision": "pass",
                    "annotation_count": 0,
                }
            ],
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state.get("result_synced") is not True
    assert state["pending_result"]["result_id"].startswith("QCR-")
    assert (cache_root / "results-pending" / job["code"] / "qc_result.json").is_file()


def test_submit_does_not_mark_synced_when_flow_response_is_not_completed(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-NOT-COMPLETED"
    asset_root.mkdir(parents=True)
    job = {
        "code": "QCJ-NOT-COMPLETED",
        "status": "in_progress",
        "asset_id": "AST-NOT-COMPLETED",
        "source_uri": str(asset_root),
        "result_upload_uri": str(
            tmp_path / "qc-results" / "AST-NOT-COMPLETED" / "QCJ-NOT-COMPLETED"
        ),
        "next_attempt": 1,
        "episodes": [{"episode_id": "AST-NOT-COMPLETED-EP0001"}],
    }
    cache_root = tmp_path / "qc-cache"
    cache = QualityCacheManager(cache_root, reserve_bytes=0)
    state_path = cache_root / "ready" / job["code"] / ".qc-cache.json"
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "schema_version": 3,
            "job_code": job["code"],
            "asset_id": job["asset_id"],
            "cache_complete": True,
            "episodes": [
                {"episode_id": "AST-NOT-COMPLETED-EP0001", "status": "ready"}
            ],
        },
    )

    class IncompleteClient:
        def report_work(self, *_args, **_kwargs):
            return None

        def submit_result(self, _job_code, **_values):
            return {"status": "in_progress"}

    with pytest.raises(QualityCacheError, match="未进入 completed"):
        cache.submit_result(
            IncompleteClient(),
            job,
            episode_results=[
                {
                    "episode_id": "AST-NOT-COMPLETED-EP0001",
                    "decision": "pass",
                    "annotation_count": 0,
                }
            ],
        )

    assert json.loads(state_path.read_text(encoding="utf-8")).get("result_synced") is not True


def test_completed_job_idempotent_retry_skips_work_heartbeat(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-COMPLETED-RETRY"
    asset_root.mkdir(parents=True)
    job = {
        "code": "QCJ-COMPLETED-RETRY",
        "status": "completed",
        "asset_id": "AST-COMPLETED-RETRY",
        "source_uri": str(asset_root),
        "result_upload_uri": str(
            tmp_path / "qc-results" / "AST-COMPLETED-RETRY" / "QCJ-COMPLETED-RETRY"
        ),
        "result_staging_uri": str(
            tmp_path / "incoming" / "AST-COMPLETED-RETRY" / "QCJ-COMPLETED-RETRY"
        ),
        "next_attempt": 1,
        "episodes": [{"episode_id": "AST-COMPLETED-RETRY-EP0001"}],
    }
    cache_root = tmp_path / "qc-cache"
    cache = QualityCacheManager(cache_root, reserve_bytes=0)
    QualityCacheManager._write_json_atomic(
        cache_root / "ready" / job["code"] / ".qc-cache.json",
        {
            "schema_version": 3,
            "job_code": job["code"],
            "asset_id": job["asset_id"],
            "cache_complete": True,
            "episodes": [
                {
                    "episode_id": "AST-COMPLETED-RETRY-EP0001",
                    "status": "ready",
                }
            ],
        },
    )
    client = FakeFlowClient(job)

    submitted = cache.submit_result(
        client,
        job,
        episode_results=[
            {
                "episode_id": "AST-COMPLETED-RETRY-EP0001",
                "decision": "pass",
                "annotation_count": 0,
            }
        ],
    )

    assert submitted["status"] == "completed"
    assert client.work_reports == []
    assert len(client.results) == 1
    assert (asset_root / "qc_result.json").is_file()


def test_legacy_annotations_are_rejected_before_result_publication(tmp_path: Path):
    """Catches labeled legacy jobs publishing NAS evidence before Flow rejects them."""
    asset_root = tmp_path / "nas" / "AST-LEGACY-ANNOTATIONS"
    episode_directory = asset_root / "episodes" / "episode_000001"
    episode_directory.mkdir(parents=True)
    primary = episode_directory / "motion.bvh"
    primary.write_bytes(b"legacy annotation payload")
    job = {
        "code": "QCJ-LEGACY-ANNOTATIONS",
        "asset_id": "AST-LEGACY-ANNOTATIONS",
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-LEGACY-ANNOTATIONS-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
            }
        ],
    }
    publish_asset_manifest(asset_root, job, ["episodes/episode_000001/motion.bvh"])
    cache = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    client = FakeFlowClient(job)
    cache.cache_job(client, job)

    with pytest.raises(QualityCacheError, match="标签库引用"):
        cache.submit_result(
            client,
            job,
            episode_results=[
                {
                    "episode_id": "AST-LEGACY-ANNOTATIONS-EP0001",
                    "decision": "pass_with_labels",
                    "annotation_count": 1,
                    "annotations": [{"annotation_id": "ann-legacy", "label_code": "body_sway"}],
                }
            ],
        )

    assert not (tmp_path / "qc-cache" / "results-pending" / job["code"]).exists()
    assert not (Path(job["result_upload_uri"]) / "attempt-0001" / "qc_result.json").exists()
    assert client.results == []


def test_unlabeled_positive_annotation_count_is_rejected_before_result_publication(
    tmp_path: Path, monkeypatch
):
    """Catches legacy unlabeled payloads claiming annotations without listing them."""
    cache = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    job = {
        "code": "QCJ-UNLABELED-POSITIVE-COUNT",
        "asset_id": "AST-UNLABELED-POSITIVE-COUNT",
        "episodes": [{"episode_id": "AST-UNLABELED-POSITIVE-COUNT-EP0001"}],
    }
    state_path = tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json"
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "schema_version": 3,
            "job_code": job["code"],
            "asset_id": job["asset_id"],
            "cache_complete": True,
            "episodes": [
                {
                    "episode_id": "AST-UNLABELED-POSITIVE-COUNT-EP0001",
                    "status": "ready",
                }
            ],
        },
    )
    publish_calls = []
    monkeypatch.setattr(cache, "_publish_result", lambda *args: publish_calls.append(args))
    client = FakeFlowClient(job)

    with pytest.raises(QualityCacheError, match="标签库引用"):
        cache.submit_result(
            client,
            job,
            episode_results=[
                {
                    "episode_id": "AST-UNLABELED-POSITIVE-COUNT-EP0001",
                    "decision": "pass_with_labels",
                    "annotation_count": 1,
                }
            ],
        )

    assert not (tmp_path / "qc-cache" / "results-pending" / job["code"]).exists()
    assert publish_calls == []
    assert client.results == []


def test_partial_label_reference_is_rejected_before_result_publication(
    tmp_path: Path, monkeypatch
):
    """Catches partial frozen references reaching NAS even when no facts exist."""
    cache = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0)
    job = {
        "code": "QCJ-PARTIAL-LABEL-REFERENCE",
        "asset_id": "AST-PARTIAL-LABEL-REFERENCE",
        "label_set_id": "task-quality",
        "episodes": [{"episode_id": "AST-PARTIAL-LABEL-REFERENCE-EP0001"}],
    }
    state_path = tmp_path / "qc-cache" / "ready" / job["code"] / ".qc-cache.json"
    QualityCacheManager._write_json_atomic(
        state_path,
        {
            "schema_version": 3,
            "job_code": job["code"],
            "asset_id": job["asset_id"],
            "cache_complete": True,
            "episodes": [
                {
                    "episode_id": "AST-PARTIAL-LABEL-REFERENCE-EP0001",
                    "status": "ready",
                }
            ],
        },
    )
    publish_calls = []
    monkeypatch.setattr(cache, "_publish_result", lambda *args: publish_calls.append(args))
    client = FakeFlowClient(job)

    with pytest.raises(QualityCacheError, match="标签库引用"):
        cache.submit_result(
            client,
            job,
            episode_results=[
                {
                    "episode_id": "AST-PARTIAL-LABEL-REFERENCE-EP0001",
                    "decision": "pass",
                    "annotation_count": 0,
                }
            ],
        )

    assert not (tmp_path / "qc-cache" / "results-pending" / job["code"]).exists()
    assert publish_calls == []
    assert client.results == []


def test_cache_job_accepts_partial_job_coverage_and_copies_only_covered_files(tmp_path: Path):
    """Catches partial Flow QC Jobs being rejected or downloading unscoped Episode files."""
    asset_root = tmp_path / "nas" / "AST-PARTIAL-001"
    first_source = asset_root / "episodes" / "episode_000001"
    second_source = asset_root / "episodes" / "episode_000002"
    first_source.mkdir(parents=True)
    second_source.mkdir(parents=True)
    first_payload = b"first covered Episode"
    second_payload = b"second unscoped Episode"
    first_primary = first_source / "motion.bvh"
    second_primary = second_source / "motion.bvh"
    first_primary.write_bytes(first_payload)
    second_primary.write_bytes(second_payload)
    full_episodes = [
        {
            "episode_id": "AST-PARTIAL-001-EP0001",
            "relative_path": "episodes/episode_000001",
            "primary_file": "motion.bvh",
            "checksum_sha256": hashlib.sha256(first_payload).hexdigest(),
        },
        {
            "episode_id": "AST-PARTIAL-001-EP0002",
            "relative_path": "episodes/episode_000002",
            "primary_file": "motion.bvh",
            "checksum_sha256": hashlib.sha256(second_payload).hexdigest(),
        },
    ]
    job = {
        "code": "QCJ-PARTIAL-001",
        "asset_id": "AST-PARTIAL-001",
        "source_uri": str(asset_root),
        "episodes": full_episodes,
    }
    full_manifest = publish_asset_manifest(
        asset_root,
        job,
        [
            "episodes/episode_000001/motion.bvh",
            "episodes/episode_000002/motion.bvh",
        ],
    )
    job["episodes"] = [full_episodes[0]]

    cached = QualityCacheManager(tmp_path / "qc-cache", reserve_bytes=0).cache_job(
        FakeFlowClient(job), job
    )

    cache_dir = Path(cached["cache_dir"])
    state = json.loads((cache_dir.parent / ".qc-cache.json").read_text(encoding="utf-8"))
    assert cached["reused"] is False
    assert (cache_dir / "episodes" / "episode_000001" / "motion.bvh").read_bytes() == first_payload
    assert not (cache_dir / "episodes" / "episode_000002").exists()
    assert state["episode_ids"] == ["AST-PARTIAL-001-EP0001"]
    assert state["asset_manifest_sha256"] == canonical_json_sha256(full_manifest)


def test_evict_expired_removes_synced_ready_cache_after_one_day(tmp_path: Path):
    cache_root = tmp_path / "cache"
    manager = QualityCacheManager(cache_root, reserve_bytes=0)
    job_root = cache_root / "ready" / "QCJ-expired"
    job_root.mkdir(parents=True)
    (job_root / ".qc-cache.json").write_text(
        json.dumps({"result_synced": True, "result_synced_at": "2026-08-09T11:59:59+00:00"}), encoding="utf-8"
    )
    (job_root / "asset.bin").write_bytes(b"expired-cache")
    expected_bytes = sum(path.stat().st_size for path in job_root.rglob("*") if path.is_file())

    summary = manager.evict_expired(now=datetime(2026, 8, 10, 12, tzinfo=timezone.utc))

    assert summary["evicted_jobs"] == ["QCJ-expired"]
    assert summary["freed_bytes"] == expected_bytes
    assert not job_root.exists()

def test_evict_expired_keeps_nonobject_state_and_download_partial(tmp_path: Path):
    cache_root = tmp_path / "cache"
    manager = QualityCacheManager(cache_root, reserve_bytes=0)
    malformed = cache_root / "ready" / "QCJ-nonobject"
    malformed.mkdir(parents=True)
    (malformed / ".qc-cache.json").write_text("[]", encoding="utf-8")
    partial = cache_root / "downloading" / "QCJ-active.partial"
    partial.mkdir(parents=True)
    (partial / "recording.bin").write_bytes(b"in-progress")

    summary = manager.evict_expired(now=datetime(2026, 8, 10, 12, tzinfo=timezone.utc))

    assert "QCJ-nonobject" in summary["failed_jobs"]
    assert summary["failed_jobs"]["QCJ-nonobject"] == "缓存状态必须是对象"
    assert malformed.is_dir()
    assert partial.is_dir()

def test_cache_job_evicts_expired_caches_before_checking_episode_disk_space(tmp_path: Path, monkeypatch):
    asset_root = tmp_path / "nas" / "AST-003"
    source = asset_root / "episodes" / "episode_000001"
    source.mkdir(parents=True)
    payload = b"motion"
    (source / "motion.bvh").write_bytes(payload)
    checksum = hashlib.sha256(payload).hexdigest()
    job = {
        "code": "QCJ-003",
        "asset_id": "AST-003",
        "source_uri": str(asset_root),
        "episodes": [{
            "episode_id": "AST-003-EP0001",
            "relative_path": "episodes/episode_000001",
            "primary_file": "motion.bvh",
            "checksum_sha256": checksum,
        }],
    }
    publish_asset_manifest(asset_root, job, ["episodes/episode_000001/motion.bvh"])
    manager = QualityCacheManager(tmp_path / "cache", reserve_bytes=0)
    calls = []

    def evict_expired():
        calls.append("evict_expired")
        return {}

    def ensure_disk_space(_):
        calls.append("ensure_disk_space")

    monkeypatch.setattr(manager, "evict_expired", evict_expired)
    monkeypatch.setattr(manager, "_ensure_disk_space", ensure_disk_space)

    manager.cache_job(FakeFlowClient(job), job)

    assert calls == ["evict_expired", "ensure_disk_space"]

def test_cache_rejects_wrong_manifest_checksum(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-002"
    source = asset_root / "episodes" / "episode_000001"
    source.mkdir(parents=True)
    (source / "motion.bvh").write_bytes(b"bad-data")
    job = {
        "code": "QCJ-002",
        "version": 1,
        "asset_id": "AST-002",
        "asset_size_bytes": 8,
        "asset_nas_uri": str(asset_root),
        "source_uri": str(asset_root),
        "episodes": [{
            "episode_id": "AST-002-EP0001",
            "relative_path": "episodes/episode_000001",
            "primary_file": "motion.bvh",
            "checksum_sha256": "0" * 64,
        }],
    }
    publish_asset_manifest(
        asset_root,
        job,
        ["episodes/episode_000001/motion.bvh"],
    )

    with pytest.raises(QualityCacheError, match="SHA-256"):
        QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
            FakeFlowClient(job), job
        )


def test_cache_accepts_dohc_metadata_manifest_without_hashing_source_files(
    tmp_path: Path, monkeypatch
):
    asset_root = tmp_path / "nas" / "AST-DOHC-METADATA"
    episode_root = asset_root / "Cafe-Refill-17"
    episode_root.mkdir(parents=True)
    primary = episode_root / "manifest.json"
    media = episode_root / "camera.bin"
    primary.write_text('{"storage_format":"jpeg-stream-v1"}', encoding="utf-8")
    media.write_bytes(b"camera-payload")
    files = []
    for source in (primary, media):
        files.append(
            {
                "relative_path": source.relative_to(asset_root).as_posix(),
                "size_bytes": source.stat().st_size,
                "modified_time_ms": source.stat().st_mtime_ns // 1_000_000,
            }
        )
    episode = {
        "episode_id": "AST-DOHC-METADATA-EP0001",
        "relative_path": "Cafe-Refill-17",
        "primary_file": "manifest.json",
        "data_format": "dohc_jpeg_v1",
        "integrity_mode": "metadata",
        "checksum_sha256": "",
        "manifest": {"integrity_mode": "metadata", "files": files},
    }
    manifest = {
        "schema_version": 2,
        "asset_id": "AST-DOHC-METADATA",
        "data_format": "dohc_jpeg_v1",
        "integrity_mode": "metadata",
        "episodes": [episode],
    }
    job = {
        "code": "QCJ-DOHC-METADATA",
        "asset_id": "AST-DOHC-METADATA",
        "asset_type": "egocentric",
        "source_uri": str(asset_root),
        "episodes": [episode],
        "asset_manifest": manifest,
        "asset_manifest_sha256": canonical_json_sha256(manifest),
    }
    real_sha256_file = platform_workflow.sha256_file

    def reject_source_hash(path, *args, **kwargs):
        assert Path(path).name == "asset_manifest.json"
        return real_sha256_file(path, *args, **kwargs)

    monkeypatch.setattr(platform_workflow, "sha256_file", reject_source_hash)
    cached = QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
        FakeFlowClient(job), job
    )

    cache_root = Path(cached["cache_dir"])
    assert (cache_root / "Cafe-Refill-17" / "manifest.json").read_bytes() == primary.read_bytes()
    assert (cache_root / "Cafe-Refill-17" / "camera.bin").read_bytes() == media.read_bytes()


def test_cache_rejects_changed_dohc_metadata_file(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-DOHC-CHANGED"
    episode_root = asset_root / "Park-Litter-009"
    episode_root.mkdir(parents=True)
    primary = episode_root / "manifest.json"
    primary.write_text('{"storage_format":"jpeg-stream-v1"}', encoding="utf-8")
    registered_mtime_ms = primary.stat().st_mtime_ns // 1_000_000
    episode = {
        "episode_id": "AST-DOHC-CHANGED-EP0001",
        "relative_path": "Park-Litter-009",
        "primary_file": "manifest.json",
        "data_format": "dohc_jpeg_v1",
        "integrity_mode": "metadata",
        "checksum_sha256": "",
        "manifest": {
            "integrity_mode": "metadata",
            "files": [{
                "relative_path": "Park-Litter-009/manifest.json",
                "size_bytes": primary.stat().st_size,
                "modified_time_ms": registered_mtime_ms + 1,
            }],
        },
    }
    manifest = {
        "schema_version": 2,
        "asset_id": "AST-DOHC-CHANGED",
        "data_format": "dohc_jpeg_v1",
        "integrity_mode": "metadata",
        "episodes": [episode],
    }
    job = {
        "code": "QCJ-DOHC-CHANGED",
        "asset_id": "AST-DOHC-CHANGED",
        "asset_type": "egocentric",
        "source_uri": str(asset_root),
        "episodes": [episode],
        "asset_manifest": manifest,
        "asset_manifest_sha256": canonical_json_sha256(manifest),
    }

    with pytest.raises(QualityCacheError, match="修改时间"):
        QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
            FakeFlowClient(job), job
        )


def test_cache_rejects_unsafe_platform_paths(tmp_path: Path):
    source = tmp_path / "nas" / "AST-003" / "episode_000001"
    source.mkdir(parents=True)
    (source / "motion.bvh").write_bytes(b"data")
    job = {
        "code": "QCJ-003",
        "version": 1,
        "asset_id": "AST-003",
        "asset_size_bytes": 4,
        "asset_nas_uri": str(source.parent),
        "source_uri": str(source.parent),
        "episodes": [{
            "episode_id": "AST-003-EP0001",
            "relative_path": "episode_000001",
            "primary_file": "../motion.bvh",
            "checksum_sha256": "",
        }],
    }
    safe_manifest = {
        "schema_version": 1,
        "asset_id": "AST-003",
        "episodes": [
            {
                "episode_id": "AST-003-EP0001",
                "relative_path": "episode_000001",
                "primary_file": "motion.bvh",
                "manifest": {
                    "files": [
                        {
                            "relative_path": "episode_000001/motion.bvh",
                            "size_bytes": 4,
                            "sha256": hashlib.sha256(b"data").hexdigest(),
                        }
                    ]
                },
            }
        ],
    }
    (source.parent / "asset_manifest.json").write_text(json.dumps(safe_manifest))
    job["asset_manifest"] = safe_manifest
    job["asset_manifest_sha256"] = canonical_json_sha256(safe_manifest)

    with pytest.raises(QualityCacheError, match="primary_file"):
        QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
            FakeFlowClient(job), job
        )


def test_cache_uses_manifest_and_ignores_unlisted_nas_files(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-004"
    episode_root = asset_root / "episodes" / "episode_000001"
    episode_root.mkdir(parents=True)
    primary = episode_root / "motion.bvh"
    primary.write_bytes(b"published-data")
    checksum = hashlib.sha256(primary.read_bytes()).hexdigest()
    job = {
        "code": "QCJ-004",
        "asset_id": "AST-004",
        "asset_nas_uri": str(asset_root),
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-004-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": checksum,
            }
        ],
    }
    publish_asset_manifest(
        asset_root,
        job,
        ["episodes/episode_000001/motion.bvh"],
    )
    old_result = asset_root / "qc" / "v1" / "qc_result.json"
    old_result.parent.mkdir(parents=True)
    old_result.write_text("old result", encoding="utf-8")
    (asset_root / "orphan.partial").write_text("partial", encoding="utf-8")

    cached = QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
        FakeFlowClient(job), job
    )

    cache_root = Path(cached["cache_dir"])
    assert (cache_root / "episodes/episode_000001/motion.bvh").is_file()
    assert not (cache_root / "qc").exists()
    assert not (cache_root / "orphan.partial").exists()


def test_cache_rejects_corrupted_metadata_listed_by_manifest(tmp_path: Path):
    asset_root = tmp_path / "nas" / "AST-005"
    episode_root = asset_root / "episodes" / "episode_000001"
    episode_root.mkdir(parents=True)
    primary = episode_root / "motion.bvh"
    metadata = episode_root / "metadata.json"
    primary.write_bytes(b"motion")
    metadata.write_bytes(b"good")
    job = {
        "code": "QCJ-005",
        "asset_id": "AST-005",
        "asset_nas_uri": str(asset_root),
        "source_uri": str(asset_root),
        "episodes": [
            {
                "episode_id": "AST-005-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(b"motion").hexdigest(),
            }
        ],
    }
    publish_asset_manifest(
        asset_root,
        job,
        [
            "episodes/episode_000001/motion.bvh",
            "episodes/episode_000001/metadata.json",
        ],
    )
    metadata.write_bytes(b"evil")

    with pytest.raises(QualityCacheError, match="SHA-256"):
        QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
            FakeFlowClient(job), job
        )
