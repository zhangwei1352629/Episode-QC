from __future__ import annotations

from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import episode_qc.web_server as web_server


FLOW_DURATION_NS = 30_156_000_000
LOCAL_DURATION_NS = 30_209_045_188


class CapturingCache:
    def __init__(self) -> None:
        self.submissions: list[list[dict[str, object]]] = []

    def submit_result(
        self,
        _client,
        _job,
        *,
        episode_results: list[dict[str, object]],
        result: dict[str, object],
    ) -> dict[str, object]:
        assert result == {"episode_count": 1}
        self.submissions.append(episode_results)
        return {"status": "completed"}


def _application_for_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    flow_duration_seconds: object,
    annotation: dict[str, object],
) -> tuple[web_server.EpisodeQcWebApplication, CapturingCache]:
    job = {
        "code": "QCJ-DURATION-GUARD",
        "status": "completed",
        "episodes": [
            {
                "episode_id": "AST-DURATION-GUARD-EP0001",
                "relative_path": "episodes/episode_000001",
                "duration_seconds": flow_duration_seconds,
            }
        ],
    }
    cache = CapturingCache()
    application = object.__new__(web_server.EpisodeQcWebApplication)
    application.paths = SimpleNamespace(db_path=tmp_path / "workspace.db")
    application._platform_lock = threading.Lock()
    application._platform_owned_jobs = set()
    application._platform_ownership_errors = {}
    application._assert_flow_enabled = lambda: None
    application._require_flow_client = lambda: object()
    application._platform_job = lambda _client, _job_code: job
    application._local_task_for_job = lambda _job_code: {
        "id": "task-duration-guard",
        "status": "completed",
    }
    application._quality_cache_manager = lambda: cache
    application._sync_platform_review_progress = lambda _job_code: None
    application._workspace_episode_mappings = lambda _job, _task: [
        {
            "episode_id": "AST-DURATION-GUARD-EP0001",
            "local_episode_id": "ep_local",
            "relative_path": "episodes/episode_000001",
        }
    ]
    application._write_workspace = lambda operation: operation()

    monkeypatch.setattr(
        web_server,
        "episode_detail",
        lambda *_args: {
            "episode": {
                "duration_ns": LOCAL_DURATION_NS,
                "quality_decision": "pass_with_labels",
                "review_status": "completed",
                "reviewer_name": "时长边界测试员",
                "annotation_count": 1,
                "reviewed_at": "2026-08-28T10:00:00+00:00",
            },
            "annotations": [annotation],
            "deleted_annotation_lineages": [],
        },
    )
    monkeypatch.setattr(
        web_server,
        "mark_qc_task_submitted",
        lambda *_args: {"status": "submitted"},
    )
    return application, cache


def test_submit_clamps_only_full_episode_annotation_to_flow_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotation = {
        "annotation_id": "ann-full-episode",
        "label_code": "task_incomplete",
        "scope": "episode",
        "start_offset_ns": 0,
        "end_offset_ns": LOCAL_DURATION_NS,
        "target_type": "global",
    }
    application, cache = _application_for_submission(
        tmp_path,
        monkeypatch,
        flow_duration_seconds="30.156",
        annotation=annotation,
    )

    response = application._submit_platform_job_once("QCJ-DURATION-GUARD")

    assert response["job"]["status"] == "completed"
    submitted = cache.submissions[0][0]["annotations"][0]
    assert submitted["end_offset_ns"] == FLOW_DURATION_NS
    assert annotation["end_offset_ns"] == LOCAL_DURATION_NS


def test_submit_rejects_true_annotation_overrun_instead_of_silently_clamping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotation = {
        "annotation_id": "ann-time-range",
        "label_code": "camera_blocked",
        "scope": "time_range",
        "start_offset_ns": 10_000_000_000,
        "end_offset_ns": LOCAL_DURATION_NS,
        "target_type": "camera",
    }
    application, cache = _application_for_submission(
        tmp_path,
        monkeypatch,
        flow_duration_seconds=30.156,
        annotation=annotation,
    )

    with pytest.raises(ValueError, match="真实越界"):
        application._submit_platform_job_once("QCJ-DURATION-GUARD")

    assert cache.submissions == []


def test_submit_keeps_annotations_when_flow_duration_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotation = {
        "annotation_id": "ann-open-ego",
        "label_slug": "custom_action",
        "scope": "time_range",
        "start_offset_ns": 1,
        "end_offset_ns": LOCAL_DURATION_NS,
        "target_type": "mocap",
    }
    application, cache = _application_for_submission(
        tmp_path,
        monkeypatch,
        flow_duration_seconds=0,
        annotation=annotation,
    )

    application._submit_platform_job_once("QCJ-DURATION-GUARD")

    assert cache.submissions[0][0]["annotations"] == [annotation]
