"""Run a collision-safe QC download/result-publish smoke test against an SMB share."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from episode_qc.platform_workflow import (
    QualityCacheManager,
    canonical_json_sha256,
    sha256_file,
)


class SmokeClient:
    def __init__(self, job: dict):
        self.job = job
        self.events: list[dict] = []
        self.submission: dict | None = None

    def claim(self, job_code: str) -> dict:
        assert job_code == self.job["code"]
        self.events.append({"event": "claim", "job_code": job_code})
        return self.job

    def report_cache(self, job_code: str, **values) -> dict:
        self.events.append({"event": "cache", "job_code": job_code, **values})
        return {**self.job, **values}

    def report_work(self, job_code: str, *, action: str, **values) -> dict:
        self.events.append(
            {"event": "work", "job_code": job_code, "action": action, **values}
        )
        return {"ok": True}

    def submit_result(self, job_code: str, **values) -> dict:
        self.submission = {"job_code": job_code, **values}
        self.events.append({"event": "submit", "job_code": job_code})
        return {"ok": True, "job_code": job_code, "result": values}


def write_json_bytes(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def ensure_new(paths: list[Path]) -> None:
    collisions = [str(path) for path in paths if path.exists()]
    if collisions:
        raise RuntimeError("refusing to overwrite existing smoke paths: " + ", ".join(collisions))


def run(args: argparse.Namespace) -> dict:
    nas_root = Path(args.nas_root)
    normalized_root = str(nas_root).rstrip("\\/").replace("/", "\\").casefold()
    if not normalized_root.endswith("\\datasets") or not nas_root.is_dir():
        raise RuntimeError(f"NAS root is unavailable or unexpected: {nas_root}")

    run_id = args.run_id
    asset_id = f"SMOKE-AST-{run_id}"
    job_code = f"SMOKE-QCJ-{run_id}"
    episode_id = f"{asset_id}-EP0001"
    source_root = nas_root / "incoming" / "qc-smoke-sources" / asset_id
    upload_root = nas_root / "qc-results" / asset_id / job_code
    staging_root = nas_root / "incoming" / "qc-results" / asset_id / job_code
    ensure_new([source_root, upload_root, staging_root])

    local_cache = Path(args.cache_root) / run_id
    if local_cache.exists():
        raise RuntimeError(f"local smoke cache already exists: {local_cache}")

    episode_dir = source_root / "episodes" / "episode_000001"
    episode_dir.mkdir(parents=True)
    source_motion = episode_dir / "motion.bvh"
    shutil.copy2(args.seed_file, source_motion)
    metadata = episode_dir / "metadata.json"
    write_json_bytes(
        metadata,
        {
            "schema_version": 1,
            "purpose": "episode-qc-nas-integration-smoke",
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "workstation": socket.gethostname(),
        },
    )

    relative_dir = "episodes/episode_000001"
    files = []
    for path in (source_motion, metadata):
        files.append(
            {
                "relative_path": f"{relative_dir}/{path.name}",
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    episode = {
        "episode_id": episode_id,
        "sequence_index": 1,
        "name": "NAS integration smoke",
        "relative_path": relative_dir,
        "primary_file": "motion.bvh",
        "data_format": "bvh",
        "size_bytes": sum(item["size_bytes"] for item in files),
        "file_count": len(files),
        "checksum_sha256": sha256_file(source_motion),
        "duration_seconds": 0.04,
        "frames_count": 4,
        "manifest": {"schema_version": 1, "files": files},
    }
    asset_manifest = {
        "schema_version": 1,
        "asset_id": asset_id,
        "data_name": f"episode_qc_smoke_{run_id}",
        "data_format": "bvh",
        "episodes": [episode],
    }
    write_json_bytes(source_root / "asset_manifest.json", asset_manifest)

    job = {
        "code": job_code,
        "asset_id": asset_id,
        "source_uri": str(source_root),
        "asset_nas_uri": str(source_root),
        "asset_manifest": asset_manifest,
        "asset_manifest_sha256": canonical_json_sha256(asset_manifest),
        "episodes": [episode],
        "result_upload_uri": str(upload_root),
        "result_staging_uri": str(staging_root),
        "next_attempt": 1,
    }
    client = SmokeClient(job)
    manager = QualityCacheManager(local_cache, reserve_bytes=0)
    cached = manager.cache_job(client, job)
    cached_motion = Path(cached["cache_dir"]) / relative_dir / "motion.bvh"
    if sha256_file(cached_motion) != episode["checksum_sha256"]:
        raise RuntimeError("downloaded file digest does not match NAS source")

    manager.start_review(client, job_code)
    result = manager.submit_result(
        client,
        job,
        episode_results=[
            {
                "episode_id": episode_id,
                "decision": "pass",
                "quality_grade": "good",
                "annotation_count": 0,
                "result": {"smoke_test": True},
            }
        ],
        result={"smoke_test": True, "run_id": run_id},
    )
    submission = client.submission or {}
    published_file = upload_root / "attempt-0001" / "qc_result.json"
    published_manifest = upload_root / "attempt-0001" / "result_manifest.json"
    if not published_file.is_file() or not published_manifest.is_file():
        raise RuntimeError("published QC result is incomplete")
    if sha256_file(published_file) != submission.get("result_sha256"):
        raise RuntimeError("published QC result digest mismatch")
    if staging_root.exists():
        unexpected = list(staging_root.iterdir())
        if unexpected:
            raise RuntimeError(f"staging directory was not drained: {unexpected}")

    return {
        "ok": True,
        "run_id": run_id,
        "asset_id": asset_id,
        "job_code": job_code,
        "nas_source": str(source_root),
        "local_cache": str(cached["cache_dir"]),
        "published_result": str(published_file),
        "source_sha256": episode["checksum_sha256"],
        "result_sha256": submission["result_sha256"],
        "cache_reused": cached["reused"],
        "event_count": len(client.events),
        "submit_ok": bool(result.get("ok")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nas-root", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--seed-file", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # Smoke command must return a concise diagnostic.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
