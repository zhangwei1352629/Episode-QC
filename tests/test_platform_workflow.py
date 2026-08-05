from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from episode_qc.platform_workflow import QualityCacheError, QualityCacheManager


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
    total_bytes = sum(path.stat().st_size for path in asset_root.rglob("*") if path.is_file())
    job = {
        "code": "QCJ-001",
        "version": 1,
        "asset_id": "AST-001",
        "asset_size_bytes": total_bytes,
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
    result_path = asset_root / "qc" / "v1" / "qc_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert [item["decision"] for item in result["episode_results"]] == [
        "pass_with_labels",
        "discard",
    ]
    assert client.results[0]["result_nas_path"] == str(result_path)

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

    with pytest.raises(QualityCacheError, match="安全的相对路径"):
        QualityCacheManager(tmp_path / "cache", reserve_bytes=0).cache_job(
            FakeFlowClient(job), job
        )
