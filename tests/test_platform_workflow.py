from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import Mock
import urllib.error

import pytest

from episode_qc.platform_workflow import (
    FlowClient,
    FlowClientError,
    QualityCacheError,
    QualityCacheManager,
    canonical_json_sha256,
)


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


def test_atomic_json_writer_uses_platform_independent_lf(tmp_path: Path):
    target = tmp_path / "result.json"
    QualityCacheManager._write_json_atomic(target, {"name": "测试"})

    assert target.read_bytes() == b'{\n  "name": "\xe6\xb5\x8b\xe8\xaf\x95"\n}\n'


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


def test_flow_job_is_fully_cached_verified_submitted_and_safely_evicted(tmp_path: Path):
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
    reused = cache.cache_job(client, job)
    assert reused["reused"] is True
    assert reused["primary_files"] == cached["primary_files"]

    cache.start_review(client, job["code"])
    assert client.work_reports == [{"action": "start", "workstation": "QC-WS-TEST"}]
    with pytest.raises(QualityCacheError, match="尚未同步"):
        cache.evict(job["code"])

    submitted = cache.submit_result(
        client,
        job,
        episode_results=[
            {
                "episode_id": "AST-001-EP0001",
                "decision": "pass_with_labels",
                "quality_grade": "good",
                "annotation_count": 2,
                "result": {"labels": ["minor_jitter"]},
            },
            {
                "episode_id": "AST-001-EP0002",
                "decision": "reject",
                "quality_grade": "invalid",
                "annotation_count": 1,
            },
        ],
        result={"reviewed_episode_count": 2},
    )

    assert submitted["status"] == "completed"
    assert client.work_reports[-1] == {"action": "heartbeat"}
    result_path = (
        Path(job["result_upload_uri"])
        / "attempt-0001"
        / "qc_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert [item["decision"] for item in result["episode_results"]] == [
        "pass_with_labels",
        "discard",
    ]
    assert client.results[0]["result_nas_path"] == str(result_path)
    assert client.results[0]["result_id"].startswith("QCR-")
    assert client.results[0]["result_manifest"]["result_sha256"] == client.results[0][
        "result_sha256"
    ]
    assert (result_path.parent / "result_manifest.json").is_file()
    assert not any(Path(job["result_staging_uri"]).glob("*.partial"))

    cache.evict(job["code"])
    assert not (tmp_path / "qc-cache" / "ready" / job["code"]).exists()


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
