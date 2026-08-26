from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import sqlite3
import struct
import threading
import time
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

import pytest

from episode_qc.playback import ACTION_FRAME_ENCODING, MOTION_FRAME_ENCODING
from episode_qc.platform_workflow import (
    FlowClientError,
    QualityCacheError,
    QualityCacheManager,
    canonical_json_sha256,
)
import episode_qc.platform_workflow as platform_workflow
import episode_qc.web_server as web_server
from episode_qc.web_server import (
    EpisodeQcRequestHandler,
    WebPaths,
    create_web_server,
    persistent_web_token,
)
from episode_qc.workspace import install_flow_label_schema, workspace_state


TOKEN = "web-test-token"
EPISODE_ID = "ep_" + "a" * 24
STREAM_ID = "str_" + "b" * 24
LOCAL_OPENER = build_opener(ProxyHandler({}))


def flow_label_schema(*, label_name: str = "躯干摆动") -> dict[str, object]:
    return {
        "schema": {
            "schema_type": "annotation_label_schema",
            "schema_version": "1.0.0",
            "label_set_id": "flow_web_labels",
            "label_set_name": "Flow Web 标签",
            "language": "zh-CN",
        },
        "severity_levels": [{"code": "normal", "name": "一般", "order": 1}],
        "actions": [{"code": "keep_with_label", "name": "保留但标记"}],
        "groups": [{"code": "motion", "name": "动作", "order": 1}],
        "labels": [
            {
                "code": "body_sway",
                "name": label_name,
                "group": "motion",
                "enabled": True,
                "annotation_scopes": ["time_range"],
                "target_types": ["global"],
                "default_severity": "normal",
                "default_action": "keep_with_label",
                "color": "#8844EE",
            }
        ],
    }


def flow_label_job(*, code: str, schema: dict[str, object]) -> dict[str, object]:
    return {
        "code": code,
        "status": "pending",
        "asset_id": f"AST-{code}",
        "task_name": "Flow 标签缓存测试",
        "label_set_id": schema["schema"]["label_set_id"],
        "label_schema_version": schema["schema"]["schema_version"],
        "label_schema_hash": canonical_json_sha256(schema),
        "label_schema": schema,
        "episodes": [
            {
                "episode_id": f"{code}-EP0001",
                "relative_path": "episodes/episode_000001",
            }
        ],
    }


@contextmanager
def running_server(
    tmp_path: Path,
    *,
    public_hosts: tuple[str, ...] = (),
    flow_enabled: bool = True,
    require_token: bool = True,
    workspace_name: str = "workspace",
):
    project_root = Path(__file__).resolve().parents[1]
    workspace_root = tmp_path / workspace_name
    paths = WebPaths(
        root=workspace_root,
        db_path=workspace_root / "workspace.db",
        cache_root=workspace_root / "cache",
        static_root=project_root / "app" / "renderer",
        default_profile=tmp_path / "missing-profile.yaml",
        default_label_schema=tmp_path / "missing-labels.yaml",
    )
    server = create_web_server(
        paths,
        token=TOKEN,
        public_hosts=public_hosts,
        flow_enabled=flow_enabled,
        require_token=require_token,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_web_application_evicts_expired_platform_cache_on_startup(tmp_path: Path):
    expired = tmp_path / "workspace" / "platform-cache" / "ready" / "QCJ-expired"
    expired.mkdir(parents=True)
    (expired / ".qc-cache.json").write_text(
        json.dumps({
            "result_synced": True,
            "result_synced_at": "2026-08-09T11:59:59+00:00",
        }),
        encoding="utf-8",
    )
    (expired / "asset.bin").write_bytes(b"synced-cache")

    with running_server(tmp_path):
        assert not expired.exists()


def test_web_application_starts_and_closes_platform_cache_cleanup(tmp_path: Path, monkeypatch):
    events = []

    class ControlledCleanup:
        def __init__(self, manager_factory, *, interval_seconds):
            self.manager_factory = manager_factory
            events.append(("created", interval_seconds))

        def start(self):
            events.append("started")

        def close(self):
            events.append("closed")

    monkeypatch.setattr(web_server, "PlatformCacheCleanup", ControlledCleanup, raising=False)

    with running_server(tmp_path):
        assert events == [("created", 3600), "started"]

    assert events == [("created", 3600), "started", "closed"]


def test_web_application_renews_only_owned_unfinished_platform_jobs(tmp_path: Path):
    class FakeFlowClient:
        def __init__(self):
            self.heartbeats = []

        def heartbeat(self, job_code):
            self.heartbeats.append(job_code)
            return {"code": job_code}

    with running_server(tmp_path) as (server, _base_url):
        client = FakeFlowClient()
        server.application._flow_client = client
        server.application._platform_owned_jobs.update(
            {"QCJ-OWNED-002", "QCJ-OWNED-001"}
        )

        server.application._heartbeat_platform_claims_once()

        assert client.heartbeats == ["QCJ-OWNED-001", "QCJ-OWNED-002"]
        assert server.application._platform_ownership_errors == {}


def test_web_application_stops_heartbeats_after_flow_reports_lost_ownership(tmp_path: Path):
    class FakeFlowClient:
        def heartbeat(self, _job_code):
            raise FlowClientError("任务已经由其他质检员接管", status_code=409)

    with running_server(tmp_path) as (server, _base_url):
        server.application._flow_client = FakeFlowClient()
        server.application._platform_owned_jobs.add("QCJ-LOST")

        server.application._heartbeat_platform_claims_once()

        assert "QCJ-LOST" not in server.application._platform_owned_jobs
        assert server.application._platform_ownership_errors == {
            "QCJ-LOST": "任务已经由其他质检员接管"
        }


def test_result_reconciliation_schedules_only_locally_completed_unsynced_jobs(
    tmp_path: Path,
    monkeypatch,
):
    tasks = [
        {"flow_job_code": "QCJ-TARGET", "status": "completed"},
        {"flow_job_code": "QCJ-LOCAL-INCOMPLETE", "status": "in_progress"},
        {"flow_job_code": "QCJ-SYNCED", "status": "completed"},
        {"flow_job_code": "QCJ-REMOTE-COMPLETED-PENDING", "status": "completed"},
        {"flow_job_code": "QCJ-REMOTE-COMPLETED-CLEAN", "status": "completed"},
    ]
    summaries = {
        "QCJ-TARGET": {"cache_complete": True, "result_synced": False},
        "QCJ-LOCAL-INCOMPLETE": {"cache_complete": True, "result_synced": False},
        "QCJ-SYNCED": {"cache_complete": True, "result_synced": True},
        "QCJ-REMOTE-COMPLETED-PENDING": {
            "cache_complete": True,
            "result_synced": False,
            "pending_result": True,
        },
        "QCJ-REMOTE-COMPLETED-CLEAN": {
            "cache_complete": True,
            "result_synced": False,
            "pending_result": False,
        },
    }

    class FakeManager:
        def cache_summary(self, job_code):
            return summaries.get(job_code)

    with running_server(tmp_path) as (server, _base_url):
        monkeypatch.setattr(web_server, "list_qc_tasks", lambda _db_path: tasks)
        monkeypatch.setattr(server.application, "_quality_cache_manager", FakeManager)
        client = object()
        server.application._flow_client = client
        scheduled = []
        monkeypatch.setattr(
            server.application._platform_executor,
            "submit",
            lambda *args: scheduled.append(args),
        )

        server.application._schedule_platform_result_reconciliation(
            client,
            {
                "jobs": [
                    {"code": "QCJ-TARGET", "status": "in_progress"},
                    {"code": "QCJ-LOCAL-INCOMPLETE", "status": "in_progress"},
                    {"code": "QCJ-SYNCED", "status": "in_progress"},
                    {"code": "QCJ-REMOTE-COMPLETED-PENDING", "status": "completed"},
                    {"code": "QCJ-REMOTE-COMPLETED-CLEAN", "status": "completed"},
                ]
            },
            source="test",
        )

        assert [item[1:] for item in scheduled] == [
            ("QCJ-TARGET", "test"),
            ("QCJ-REMOTE-COMPLETED-PENDING", "test"),
        ]


def test_result_reconciliation_persists_failure_and_releases_single_flight(
    tmp_path: Path,
    monkeypatch,
):
    recorded = []

    class FakeManager:
        def record_result_sync_error(self, job_code, error):
            recorded.append((job_code, error))

    with running_server(tmp_path) as (server, _base_url):
        monkeypatch.setattr(server.application, "_quality_cache_manager", FakeManager)
        monkeypatch.setattr(
            server.application,
            "_submit_platform_job_once",
            lambda _job_code: (_ for _ in ()).throw(FlowClientError("Flow offline")),
        )
        server.application._platform_result_jobs.add("QCJ-RETRY")

        server.application._reconcile_platform_result("QCJ-RETRY", "test")

        assert recorded == [("QCJ-RETRY", "Flow offline")]
        assert "QCJ-RETRY" not in server.application._platform_result_jobs


def test_cleanup_failure_does_not_stop_the_web_server(tmp_path: Path, monkeypatch, caplog):
    def fail_cleanup(_manager):
        raise RuntimeError("cleanup disk error")

    monkeypatch.setattr(QualityCacheManager, "evict_expired", fail_cleanup)

    with running_server(tmp_path) as (_, base_url):
        assert request_json(f"{base_url}/api/workspace")

    assert "platform cache cleanup failed source=startup" in caplog.text


def test_renderer_separates_previous_review_from_editable_annotations():
    renderer_root = Path(__file__).resolve().parents[1] / "app" / "renderer"
    html = (renderer_root / "index.html").read_text(encoding="utf-8")
    script = (renderer_root / "renderer.js").read_text(encoding="utf-8")
    styles = (renderer_root / "styles.css").read_text(encoding="utf-8")

    assert 'id="previous-review-section"' in html
    assert 'id="toggle-previous-review"' in html
    assert 'id="toggle-current-annotations"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="previous-annotation-track"' in html
    assert 'data-track-label="历史"' in html
    assert 'data-track-label="本轮"' in html
    assert "上一轮质检" in html
    assert "只读对照" in html
    assert "function renderPreviousReview()" in script
    assert "state.detail?.episode?.previous_review" in script
    assert "来源：Flow 历史质检事实" in script
    assert 'class="previous-review-item"' in script
    assert 'class="history-annotation-block"' in script
    assert "data-previous-start-ns" in script
    assert "seekTo(Number(item.dataset.previousStartNs))" in script
    assert "function setPreviousReviewExpanded(" in script
    assert "function setCurrentAnnotationsExpanded(" in script
    assert 'classList.toggle("expanded", expanded)' in script
    assert 'episodeQcPreviousReviewExpanded' in script
    assert 'episodeQcCurrentAnnotationsExpanded' in script
    assert "历史 ${previousAnnotations.length}" in script
    assert "历史结论" in script
    assert ".history-annotation-block" in styles
    assert ".history-label-badge" in styles
    assert ".previous-review-section:not(.expanded)" in styles
    assert ".annotations-section:not(.expanded)" in styles
    assert "grid-template-rows: minmax(0, 1fr) auto auto auto" in styles
    assert 'data-annotation-id=' not in script.split(
        "function renderPreviousReview()", 1
    )[1].split("function openAnnotationEditor", 1)[0]


def test_existing_cached_job_refreshes_previous_review_from_flow_detail(
    tmp_path: Path, monkeypatch
):
    job = {
        "code": "QCJ-HISTORY-REFRESH",
        "episodes": [
            {
                "episode_id": "AST-HISTORY-EP0001",
                "previous_review": {"decision": "pass", "annotations": []},
            }
        ],
    }
    mappings = [
        {
            "episode_id": "AST-HISTORY-EP0001",
            "local_episode_id": "ep_local",
        }
    ]

    class FakeClient:
        def job(self, job_code):
            assert job_code == job["code"]
            return job

    class FakeCache:
        def local_episode_mappings(self, job_code):
            assert job_code == job["code"]
            return mappings

    synced = []
    monkeypatch.setattr(
        web_server,
        "sync_flow_previous_reviews",
        lambda db_path, payload, local_mappings: synced.append(
            (db_path, payload, local_mappings)
        ),
    )
    with running_server(tmp_path) as (server, _base_url):
        server.application._local_task_for_job = lambda _code: {"id": "task-local"}
        server.application._quality_cache_manager = lambda: FakeCache()

        assert server.application._platform_job(FakeClient(), job["code"]) == job

    assert synced == [(server.application.paths.db_path, job, mappings)]


def test_existing_cached_job_rebuilds_previous_review_mapping_when_cache_state_is_missing(
    tmp_path: Path, monkeypatch
):
    job = {
        "code": "QCJ-HISTORY-LEGACY-CACHE",
        "episodes": [
            {
                "episode_id": "AST-HISTORY-EP0001",
                "relative_path": "episodes/episode_000001",
                "previous_review": {
                    "decision": "pass_with_labels",
                    "annotations": [{"label_code": "legacy-label"}],
                },
            }
        ],
    }

    class FakeClient:
        def job(self, job_code):
            assert job_code == job["code"]
            return job

    class MissingCacheState:
        def local_episode_mappings(self, job_code):
            assert job_code == job["code"]
            raise QualityCacheError("cache state is missing")

    monkeypatch.setattr(
        web_server,
        "workspace_state",
        lambda _db_path, *, task_id: {
            "episodes": [
                {
                    "id": "ep_local",
                    "relative_path": "episodes\\episode_000001",
                }
            ]
            if task_id == "task-local"
            else [],
        },
    )
    synced = []
    monkeypatch.setattr(
        web_server,
        "sync_flow_previous_reviews",
        lambda db_path, payload, local_mappings: synced.append(
            (db_path, payload, local_mappings)
        ),
    )
    with running_server(tmp_path) as (server, _base_url):
        server.application._local_task_for_job = lambda _code: {
            "id": "task-local",
            "flow_job_code": job["code"],
        }
        server.application._quality_cache_manager = lambda: MissingCacheState()

        assert server.application._platform_job(FakeClient(), job["code"]) == job

    assert synced == [
        (
            server.application.paths.db_path,
            job,
            [
                {
                    "episode_id": "AST-HISTORY-EP0001",
                    "local_episode_id": "ep_local",
                    "relative_path": "episodes/episode_000001",
                }
            ],
        )
    ]


def test_web_flow_label_schema_is_installed_before_ready_episode_indexing(tmp_path: Path, monkeypatch):
    schema = flow_label_schema()
    job = flow_label_job(code="QCJ-WEB-LABEL-SCHEMA", schema=schema)
    indexed = []

    class FakeFlowClient:
        def jobs(self):
            return [dict(job)]

        def claim(self, _job_code):
            return dict(job) | {"status": "claimed"}

        def report_cache(self, _job_code, **values):
            job.update(values)
            return dict(job)

    class FakeCache:
        def cache_job(self, _client, _job, *, progress_callback, episode_ready_callback):
            progress_callback({"status": "caching", "progress": 50})
            episode_ready_callback(
                {
                    "cache_dir": str(tmp_path / "cached"),
                    "cached_episode_count": 1,
                    "total_episode_count": 1,
                }
            )
            return {"total_episode_count": 1}

        def record_local_episodes(self, _job_code, mappings):
            assert mappings == [
                {
                    "episode_id": "QCJ-WEB-LABEL-SCHEMA-EP0001",
                    "local_episode_id": "ep_local",
                    "relative_path": "episodes/episode_000001",
                }
            ]

        def cache_summary(self, _job_code):
            return {"cached_bytes": 1, "total_bytes": 1}

        def start_review(self, _client, _job_code):
            return {"status": "in_progress"}

    def scan_after_schema_install(db_path, *_args, **_kwargs):
        assert _kwargs["label_set_id"]
        with sqlite3.connect(db_path) as connection:
            stored = connection.execute(
                "SELECT raw_schema_json FROM label_set WHERE id = ?",
                (_kwargs["label_set_id"],),
            ).fetchone()
        assert json.loads(stored[0]) == schema
        indexed.append(True)
        return {
            "task_id": "task_local",
            "episodes": [
                {
                    "id": "ep_local",
                    "relative_path": "episodes/episode_000001",
                    "import_status": "ready",
                }
            ],
        }

    monkeypatch.setattr(web_server, "scan_data_source", scan_after_schema_install)
    with running_server(tmp_path) as (server, _base_url):
        server.application._quality_cache_manager = lambda: FakeCache()
        server.application._cache_platform_job(FakeFlowClient(), job["code"])

    assert indexed == [True]
    assert job.get("status") != "failed"


def test_web_flow_label_schema_conflict_persists_failed_report_and_reconnects_without_recaching(tmp_path: Path, monkeypatch):
    local_schema = flow_label_schema(label_name="本地冲突标签")
    frozen_schema = flow_label_schema()
    local_job = flow_label_job(code="QCJ-LOCAL-LABEL-SCHEMA", schema=local_schema)
    job = flow_label_job(code="QCJ-WEB-LABEL-CONFLICT", schema=frozen_schema)

    class FakeFlowClient:
        def __init__(self):
            self.available = False
            self.reports = []
            self.submitted = []

        def jobs_response(self):
            return {"reviewer": "Web 质检员", "jobs": [dict(job)]}

        def jobs(self):
            return [dict(job)]

        def claim(self, job_code):
            assert job_code == job["code"]
            job["status"] = "claimed"
            return dict(job)

        def report_cache(self, job_code, **values):
            assert job_code == job["code"]
            self.reports.append((job_code, values))
            if not self.available:
                raise FlowClientError("temporary Flow outage")
            job.update(values)
            return dict(job)

        def submit_result(self, job_code, **values):
            self.submitted.append((job_code, values))
            return dict(job)

    cache_calls = []
    scan_calls = []
    client = FakeFlowClient()
    monkeypatch.setattr(platform_workflow.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(web_server, "scan_data_source", lambda *_args, **_kwargs: scan_calls.append(True))
    with running_server(tmp_path) as (server, base_url):
        install_flow_label_schema(server.application.paths.db_path, local_job)
        server.application._flow_client = client
        cache = QualityCacheManager(
            server.application.paths.root / "platform-cache", reserve_bytes=0
        )
        monkeypatch.setattr(
            cache,
            "cache_job",
            lambda *_args, **_kwargs: cache_calls.append(True),
        )
        server.application._quality_cache_manager = lambda: cache

        status, accepted = request_json(
            f"{base_url}/api/platform/jobs/{job['code']}/claim", method="POST"
        )
        assert status == 202 and accepted["accepted"] is True
        for _ in range(200):
            _status, payload = request_json(f"{base_url}/api/platform/jobs")
            visible = payload["jobs"][0]
            if len(client.reports) >= 3 and not visible["local_caching"]:
                break
            time.sleep(0.01)

        state_path = (
            server.application.paths.root
            / "platform-cache"
            / "failed"
            / job["code"]
            / ".qc-cache.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert visible["cache_status"] == "failed"
        assert "同一标签集版本已有不同的本地标签快照" in visible["cache_error"]
        assert state["pending_cache_report"]["status"] == "failed"
        assert len(client.reports) == 3
        assert cache_calls == []
        assert scan_calls == []
        assert client.submitted == []

        client.available = True
        server.application._flow_client_factory = lambda *_args: client
        status, reconnected = request_json(
            f"{base_url}/api/platform/login",
            method="POST",
            payload={
                "baseUrl": "http://flow.test:8000",
                "username": "reviewer",
                "password": "secret",
            },
        )

        assert status == 200
        assert len(client.reports) == 4
        assert client.reports[-1] == (
            job["code"],
            {
                "status": "failed",
                "cache_error": "同一标签集版本已有不同的本地标签快照",
            },
        )
        assert "pending_cache_report" not in json.loads(state_path.read_text(encoding="utf-8"))
        assert cache_calls == []
        assert scan_calls == []
        assert reconnected["jobs"][0]["cache_status"] == "failed"

        _status, refreshed = request_json(f"{base_url}/api/platform/jobs")
        assert refreshed["jobs"][0]["status"] == "failed"
        assert "同一标签集版本已有不同的本地标签快照" in refreshed["jobs"][0]["cache_error"]


def test_web_existing_ready_cache_failure_retries_its_own_durable_report_without_recaching(tmp_path: Path, monkeypatch):
    job = {"code": "QCJ-WEB-READY-FAILURE", "status": "in_progress", "asset_id": "AST-READY"}

    class FakeFlowClient:
        def __init__(self):
            self.available = False
            self.reports = []

        def jobs(self):
            return [dict(job)]

        def report_cache(self, job_code, **values):
            self.reports.append((job_code, values))
            if not self.available:
                raise FlowClientError("temporary Flow outage")
            job.update(values)
            return dict(job)

    client = FakeFlowClient()
    monkeypatch.setattr(platform_workflow.time, "sleep", lambda _delay: None)
    with running_server(tmp_path) as (server, _base_url):
        cache = QualityCacheManager(
            server.application.paths.root / "platform-cache", reserve_bytes=0
        )
        ready_state_path = (
            server.application.paths.root
            / "platform-cache"
            / "ready"
            / job["code"]
            / ".qc-cache.json"
        )
        QualityCacheManager._write_json_atomic(
            ready_state_path,
            {
                "schema_version": 3,
                "job_code": job["code"],
                "asset_id": job["asset_id"],
                "cache_complete": True,
                "cache_status": "cache_ready",
                "cached_episode_count": 1,
                "total_episode_count": 1,
                "cached_bytes": 42,
                "total_bytes": 42,
                "local_episodes": [{"episode_id": "FLOW-EP", "local_episode_id": "ep_local"}],
                "result_synced": False,
            },
        )
        monkeypatch.setattr(
            cache,
            "cache_job",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(QualityCacheError("index failed")),
        )
        server.application._quality_cache_manager = lambda: cache
        server.application._cache_platform_job(client, job["code"])

        ready_state = json.loads(ready_state_path.read_text(encoding="utf-8"))
        pre_state_path = (
            server.application.paths.root
            / "platform-cache"
            / "failed"
            / job["code"]
            / ".qc-cache.json"
        )
        assert ready_state["cache_status"] == "failed"
        assert ready_state["cache_error"] == "index failed"
        assert ready_state["pending_cache_report"] == {
            "status": "failed",
            "cache_error": "index failed",
        }
        assert ready_state["local_episodes"] == [{"episode_id": "FLOW-EP", "local_episode_id": "ep_local"}]
        assert ready_state["result_synced"] is False
        assert not pre_state_path.exists()
        assert len(client.reports) == 3

        scheduled = []
        monkeypatch.setattr(
            server.application._platform_executor,
            "submit",
            lambda *_args: scheduled.append(True),
        )
        client.available = True
        server.application._resume_incomplete_platform_caches(client, {"jobs": [dict(job)]})

        assert client.reports[-1] == (
            job["code"],
            {"status": "failed", "cache_error": "index failed"},
        )
        assert "pending_cache_report" not in json.loads(ready_state_path.read_text(encoding="utf-8"))
        assert scheduled == []


def test_web_failed_claim_keeps_pre_cache_failure_journal_and_does_not_start_cache(tmp_path: Path, monkeypatch):
    job = {"code": "QCJ-WEB-CLAIM-FAILURE", "status": "pending"}

    class FailingClaimClient:
        def jobs(self):
            return [dict(job)]

        def claim(self, _job_code):
            raise FlowClientError("Flow claim unavailable")

    with running_server(tmp_path) as (server, _base_url):
        cache = QualityCacheManager(
            server.application.paths.root / "platform-cache", reserve_bytes=0
        )
        cache.record_pre_cache_failure(job["code"], "frozen schema conflict")
        scheduled = []
        monkeypatch.setattr(
            server.application._platform_executor,
            "submit",
            lambda *_args: scheduled.append(True),
        )
        server.application._flow_client = FailingClaimClient()
        server.application._quality_cache_manager = lambda: cache

        with pytest.raises(FlowClientError, match="Flow claim unavailable"):
            server.application.claim_platform_job(job["code"])

        assert cache.has_pre_cache_failure(job["code"])
        assert scheduled == []


def test_web_reclaims_pending_flow_job_when_local_cache_is_complete(tmp_path: Path, monkeypatch):
    job = {
        "code": "QCJ-WEB-RECLAIM-COMPLETE",
        "status": "pending",
        "lease_expired": True,
    }
    claimed_job = {**job, "status": "claimed", "lease_expired": False}

    class FakeFlowClient:
        def __init__(self):
            self.claimed = []

        def jobs(self):
            return [dict(job)]

        def claim(self, job_code):
            self.claimed.append(job_code)
            return dict(claimed_job)

    class FakeCache:
        def cache_summary(self, _job_code):
            return {"cache_complete": True}

        def has_pre_cache_failure(self, _job_code):
            return False

    with running_server(tmp_path) as (server, _base_url):
        client = FakeFlowClient()
        scheduled = []
        server.application._flow_client = client
        monkeypatch.setattr(
            server.application,
            "_local_task_for_job",
            lambda _job_code: {"status": "completed"},
        )
        monkeypatch.setattr(server.application, "_quality_cache_manager", FakeCache)
        monkeypatch.setattr(
            server.application._platform_executor,
            "submit",
            lambda *args: scheduled.append(args),
        )

        response = server.application.claim_platform_job(job["code"])

        assert response == {"accepted": True, "job": claimed_job, "caching": True}
        assert client.claimed == [job["code"]]
        assert len(scheduled) == 1
        assert scheduled[0][1:] == (client, job["code"])


def test_web_exposes_missing_cache_state_and_backs_up_before_recovery(
    tmp_path: Path,
    monkeypatch,
):
    job = {
        "code": "QCJ-WEB-RECOVER-MISSING-CACHE",
        "status": "pending",
        "claimable": True,
    }
    events = []

    class FakeFlowClient:
        def jobs_response(self):
            return {"reviewer": "Web 质检员", "jobs": [dict(job)]}

        def jobs(self):
            return [dict(job)]

        def claim(self, job_code):
            events.append(("claim", job_code))
            return {**job, "status": "claimed"}

    class FakeCache:
        def cache_summary(self, _job_code):
            return None

        def has_pre_cache_failure(self, _job_code):
            return False

    backup_path = tmp_path / "workspace-backups" / "recovery.db"
    monkeypatch.setattr(
        web_server,
        "backup_workspace_database",
        lambda *_args, **_kwargs: events.append(("backup", job["code"])) or backup_path,
        raising=False,
    )
    with running_server(tmp_path) as (server, _base_url):
        client = FakeFlowClient()
        cache = FakeCache()
        local_task = {
            "id": "task-local",
            "status": "completed",
            "completed_count": 2,
            "flow_job_code": job["code"],
        }
        server.application._flow_client = client
        monkeypatch.setattr(web_server, "list_qc_tasks", lambda *_args: [local_task])
        monkeypatch.setattr(
            server.application,
            "_local_task_for_job",
            lambda _job_code: local_task,
        )
        monkeypatch.setattr(server.application, "_quality_cache_manager", lambda: cache)
        monkeypatch.setattr(
            server.application._platform_executor,
            "submit",
            lambda *args: events.append(("submit", args[2])),
        )

        payload = server.application.get_platform_jobs()
        assert payload["jobs"][0]["cache_state_missing"] is True
        assert payload["jobs"][0]["cache_recovery_available"] is True

        response = server.application.claim_platform_job(job["code"])

        assert events == [
            ("backup", job["code"]),
            ("claim", job["code"]),
            ("submit", job["code"]),
        ]
        assert response == {
            "accepted": True,
            "job": {**job, "status": "claimed"},
            "caching": True,
            "cache_recovery": True,
            "workspace_backup": backup_path.name,
        }


def test_web_starts_completed_local_task_when_flow_needs_review_session(tmp_path: Path, monkeypatch):
    job = {"code": "QCJ-WEB-RESTART-REVIEW", "status": "cache_ready"}

    class FakeFlowClient:
        def jobs(self):
            return [dict(job)]

    class FakeCache:
        def __init__(self):
            self.started = []

        def start_review(self, _client, job_code):
            self.started.append(job_code)
            job["status"] = "in_progress"
            return dict(job)

    with running_server(tmp_path) as (server, _base_url):
        cache = FakeCache()
        server.application._flow_client = FakeFlowClient()
        monkeypatch.setattr(
            server.application,
            "_local_task_for_job",
            lambda _job_code: {"status": "completed"},
        )
        monkeypatch.setattr(server.application, "_quality_cache_manager", lambda: cache)

        response = server.application.start_platform_job(job["code"])

        assert response["started"] is True
        assert response["job"]["status"] == "in_progress"
        assert cache.started == [job["code"]]


def test_web_submit_reactivates_expired_flow_job_before_upload(tmp_path: Path, monkeypatch):
    job = {
        "code": "QCJ-WEB-REACTIVATE-SUBMIT",
        "status": "pending",
        "lease_expired": True,
    }
    calls = []

    class FakeFlowClient:
        def jobs(self):
            return [dict(job)]

        def claim(self, job_code):
            calls.append(("claim", job_code))
            job.update(status="claimed", lease_expired=False)
            return dict(job)

    class FakeCache:
        def local_episode_mappings(self, _job_code):
            return [{"episode_id": "FLOW-EP-1", "local_episode_id": "ep_local"}]

        def start_review(self, _client, job_code):
            calls.append(("start", job_code))
            job["status"] = "in_progress"
            return dict(job)

        def submit_result(self, _client, submitted_job, *, episode_results, result):
            calls.append(("submit", submitted_job["status"]))
            return {"status": "completed"}

    monkeypatch.setattr(
        web_server,
        "episode_detail",
        lambda *_args: {
            "episode": {
                "quality_decision": "pass",
                "review_status": "completed",
                "reviewer_name": "Web 质检员",
                "annotation_count": 0,
            },
            "annotations": [],
        },
    )
    with running_server(tmp_path) as (server, _base_url):
        server.application._flow_client = FakeFlowClient()
        server.application._quality_cache_manager = lambda: FakeCache()
        monkeypatch.setattr(
            web_server.EpisodeQcWebApplication,
            "_local_task_for_job",
            lambda *_args: {"id": "task_local", "status": "completed"},
        )
        monkeypatch.setattr(
            web_server,
            "mark_qc_task_submitted",
            lambda *_args: {"status": "submitted"},
        )

        response = server.application.submit_platform_job(job["code"])

    assert response["job"]["status"] == "completed"
    assert calls == [
        ("claim", job["code"]),
        ("start", job["code"]),
        ("submit", "in_progress"),
    ]


def test_web_submit_resumes_paused_work_session_for_in_progress_job(
    tmp_path: Path,
    monkeypatch,
):
    job = {
        "code": "QCJ-WEB-RESUME-SUBMIT",
        "status": "in_progress",
        "lease_expired": False,
    }
    calls = []

    class FakeFlowClient:
        def jobs(self):
            return [dict(job)]

        def claim(self, job_code):
            calls.append(("claim", job_code))
            return dict(job)

    class FakeCache:
        def local_episode_mappings(self, _job_code):
            return [{"episode_id": "FLOW-EP-1", "local_episode_id": "ep_local"}]

        def start_review(self, _client, job_code):
            calls.append(("start", job_code))
            return dict(job)

        def submit_result(self, _client, submitted_job, *, episode_results, result):
            calls.append(("submit", submitted_job["status"]))
            return {"status": "completed"}

    monkeypatch.setattr(
        web_server,
        "episode_detail",
        lambda *_args: {
            "episode": {
                "quality_decision": "pass",
                "review_status": "completed",
                "reviewer_name": "Web 质检员",
                "annotation_count": 0,
            },
            "annotations": [],
        },
    )
    with running_server(tmp_path) as (server, _base_url):
        server.application._flow_client = FakeFlowClient()
        server.application._quality_cache_manager = lambda: FakeCache()
        monkeypatch.setattr(
            web_server.EpisodeQcWebApplication,
            "_local_task_for_job",
            lambda *_args: {"id": "task_local", "status": "completed"},
        )
        monkeypatch.setattr(
            web_server,
            "mark_qc_task_submitted",
            lambda *_args: {"status": "submitted"},
        )

        response = server.application.submit_platform_job(job["code"])

    assert response["job"]["status"] == "completed"
    assert calls == [
        ("start", job["code"]),
        ("submit", "in_progress"),
    ]


def test_web_flow_label_schema_submit_uses_direct_annotations(tmp_path: Path, monkeypatch):
    job = {"code": "QCJ-WEB-LABEL-SUBMIT", "status": "in_progress"}
    annotation = {"annotation_id": "ann_" + "c" * 24, "label_code": "body_sway"}
    submitted = {}

    class FakeFlowClient:
        def jobs(self):
            return [dict(job)]

    class FakeCache:
        def local_episode_mappings(self, _job_code):
            return [{"episode_id": "FLOW-EP-1", "local_episode_id": "ep_local"}]

        def start_review(self, _client, _job_code):
            return dict(job)

        def submit_result(self, _client, _job, *, episode_results, result):
            submitted["episode_results"] = episode_results
            submitted["result"] = result
            return {"status": "completed"}

    monkeypatch.setattr(
        web_server,
        "episode_detail",
        lambda *_args: {
            "episode": {
                "quality_decision": "pass_with_labels",
                "review_status": "completed",
                "reviewer_name": "Web 质检员",
                "annotation_count": 1,
            },
            "annotations": [annotation],
        },
    )
    with running_server(tmp_path) as (server, _base_url):
        server.application._flow_client = FakeFlowClient()
        server.application._quality_cache_manager = lambda: FakeCache()
        monkeypatch.setattr(web_server.EpisodeQcWebApplication, "_local_task_for_job", lambda *_args: {"id": "task_local"})
        monkeypatch.setattr(web_server, "mark_qc_task_submitted", lambda *_args: {"status": "submitted"})
        response = server.application.submit_platform_job(job["code"])

    assert response["job"]["status"] == "completed"
    episode_result = submitted["episode_results"][0]
    assert episode_result["annotation_count"] == 1
    assert episode_result["annotations"] == [annotation]
    assert episode_result["result"] == {
        "local_episode_id": "ep_local",
        "review_status": "completed",
        "reviewer_name": "Web 质检员",
    }

def request_json(url: str, *, method: str = "GET", payload: dict[str, object] | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "X-Episode-QC-Token": TOKEN,
            "Content-Type": "application/json",
            "Origin": url.split("/api/", 1)[0],
        },
    )
    with LOCAL_OPENER.open(request, timeout=5) as response:
        return response.status, json.loads(response.read())


def test_web_api_requires_token_and_serves_workspace(tmp_path: Path):
    with running_server(tmp_path) as (server, base_url):
        with LOCAL_OPENER.open(f"{base_url}/", timeout=5) as response:
            assert response.status == 200
            assert response.geturl() == f"{base_url}/?token={TOKEN}"
            assert "Episode 质检".encode() in response.read()

        with LOCAL_OPENER.open(f"{base_url}/?token=stale-token", timeout=5) as response:
            assert response.status == 200
            assert response.geturl() == f"{base_url}/?token={TOKEN}"

        with pytest.raises(HTTPError) as error:
            LOCAL_OPENER.open(f"{base_url}/api/workspace", timeout=5)
        assert error.value.code == 401
        assert json.loads(error.value.read())["error"] == "访问令牌无效"

        status, state = request_json(f"{base_url}/api/workspace")
        assert status == 200
        captured_export = {}
        server.application.export = lambda request: captured_export.update(request) or {
            "format": request.get("format"),
            "output_file": str(tmp_path / "任务_标注结果.csv"),
        }
        status, exported = request_json(
            f"{base_url}/api/export",
            method="POST",
            payload={"outputParent": str(tmp_path / "exports"), "format": "csv", "episodeIds": []},
        )
        assert status == 200
        assert exported["format"] == "csv"
        assert captured_export["format"] == "csv"
        assert Path(exported["output_file"]).name == "任务_标注结果.csv"
        assert state["workspace"]["name"] == "Mocap QC 工作区"

        status, workspace = request_json(
            f"{base_url}/api/workspace/settings",
            method="POST",
            payload={"reviewer": "Web 测试员"},
        )
        assert status == 200
        assert workspace["reviewer_name"] == "Web 测试员"

        with LOCAL_OPENER.open(f"{base_url}/web-api.js", timeout=5) as response:
            assert response.status == 200
            assert b"window.episodeQc" in response.read()
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert response.headers["X-Frame-Options"] == "DENY"

        with LOCAL_OPENER.open(f"{base_url}/label-template-simple.yaml", timeout=5) as response:
            assert response.status == 200
            template = response.read().decode("utf-8")
            assert "标签库名称" in template
            assert "编码和名称必填" in template

        with LOCAL_OPENER.open(f"{base_url}/target-selection.mjs", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/javascript")
            assert b"resolveSelectedTarget" in response.read()


def test_explicit_no_token_mode_allows_api_without_token(tmp_path: Path):
    with running_server(tmp_path, require_token=False) as (_server, base_url):
        with LOCAL_OPENER.open(f"{base_url}/", timeout=5) as response:
            assert response.status == 200
            assert response.geturl() == f"{base_url}/"
        with LOCAL_OPENER.open(f"{base_url}/api/workspace", timeout=5) as response:
            assert response.status == 200
            assert json.loads(response.read())["workspace"]["name"] == "Mocap QC 工作区"


def test_health_reports_unavailable_configured_nas_without_blocking_web_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A lost NAS share must be visible to QC users, not prevent local QC access."""
    unavailable_nas = tmp_path / "unavailable-nas"
    monkeypatch.setenv("EPISODE_QC_NAS_PROBE_PATH", str(unavailable_nas))

    with running_server(tmp_path) as (_server, base_url):
        status, health = request_json(f"{base_url}/api/health")

    assert status == 200
    assert health["ok"] is True
    assert health["nas"] == {
        "configured": True,
        "available": False,
        "path": str(unavailable_nas),
        "message": "NAS 当前不可用；可继续查看本机已有任务，依赖 NAS 的领取、缓存、导入和提交操作将在恢复后可用。",
    }


def test_health_returns_cached_nas_status_while_slow_probe_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A stalled UNC check must not delay the health response."""
    unavailable_nas = tmp_path / "slow-unavailable-nas"
    probe_started = threading.Event()
    release_probe = threading.Event()
    original_is_dir = Path.is_dir

    def slow_is_dir(path: Path) -> bool:
        if path == unavailable_nas:
            probe_started.set()
            release_probe.wait(timeout=1)
            return False
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", slow_is_dir)
    monkeypatch.setenv("EPISODE_QC_NAS_PROBE_PATH", str(unavailable_nas))

    try:
        with running_server(tmp_path) as (_server, base_url):
            assert probe_started.wait(timeout=0.25)
            started_at = time.monotonic()
            status, health = request_json(f"{base_url}/api/health")
            elapsed = time.monotonic() - started_at
    finally:
        release_probe.set()

    assert status == 200
    assert elapsed < 0.25
    assert health["nas"]["available"] is False


def test_only_loopback_clients_may_auto_receive_web_token():
    handler = object.__new__(EpisodeQcRequestHandler)
    handler.client_address = ("127.0.0.1", 12345)
    assert handler._client_is_loopback() is True

    handler.client_address = ("10.1.10.99", 12345)
    assert handler._client_is_loopback() is False


def test_platform_jobs_return_active_verification_progress_and_renderer_contract(tmp_path: Path):
    job = {
        "code": "QCJ-WEB-PROGRESS",
        "status": "caching",
        "asset_id": "AST-WEB-PROGRESS",
    }
    progress = {
        "status": "verifying",
        "phase": "verifying",
        "progress": 99,
        "verified_files": 3,
        "total_files": 9,
        "current_file": "episodes/episode_000003/data.mcap",
    }

    class FakeFlowClient:
        def jobs_response(self):
            return {"reviewer": "Web 质检员", "jobs": [dict(job)]}

    with running_server(tmp_path) as (server, base_url):
        server.application._flow_client = FakeFlowClient()
        with server.application._platform_lock:
            server.application._platform_jobs.add(job["code"])
            server.application._platform_progress = {job["code"]: dict(progress)}

        status, payload = request_json(f"{base_url}/api/platform/jobs")
        assert status == 200
        visible = payload["jobs"][0]
        assert visible["local_caching"] is True
        assert visible["local_progress"] == progress

        with server.application._platform_lock:
            server.application._platform_jobs.discard(job["code"])
        status, payload = request_json(f"{base_url}/api/platform/jobs")
        assert status == 200
        assert "local_progress" not in payload["jobs"][0]

    renderer = (Path(__file__).resolve().parents[1] / "app" / "renderer" / "renderer.js").read_text(
        encoding="utf-8"
    )
    phase_marker = "local.phase === " + chr(34) + "verifying" + chr(34)
    assert phase_marker in renderer
    assert "校验 ${Number(local.verified_files || 0)}/${Number(local.total_files)} 个文件" in renderer
    assert "job.local_caching || job.cache_complete === false" in renderer


def test_platform_cache_indexes_ready_episode_while_job_still_caching(tmp_path: Path):
    source = tmp_path / "nas" / "AST-WEB-PROGRESSIVE"
    payload = b"""HIERARCHY
ROOT Hips
{
  OFFSET 0 0 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
  End Site
  {
    OFFSET 0 10 0
  }
}
MOTION
Frames: 2
Frame Time: 0.010000
0 0 0 0 0 0
1 0 0 0 0 0
"""
    episodes = []
    for index in (1, 2):
        relative_path = f"episodes/episode_{index:06d}"
        primary = source / relative_path / "motion.bvh"
        primary.parent.mkdir(parents=True, exist_ok=True)
        primary.write_bytes(payload + str(index).encode("ascii"))
        episodes.append(
            {
                "episode_id": f"AST-WEB-PROGRESSIVE-EP{index:04d}",
                "relative_path": relative_path,
                "primary_file": "motion.bvh",
                "checksum_sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
            }
        )
    job = {
        "code": "QCJ-WEB-PROGRESSIVE",
        "status": "pending",
        "asset_id": "AST-WEB-PROGRESSIVE",
        "task_name": "渐进缓存测试",
        "source_uri": str(source),
        "episodes": episodes,
    }
    first_ready = threading.Event()
    continue_cache = threading.Event()
    work_reports = []

    class FakeFlowClient:
        def jobs_response(self):
            return {"reviewer": "Web 质检员", "jobs": [dict(job)]}

        def jobs(self):
            return [dict(job)]

        def claim(self, job_code):
            assert job_code == job["code"]
            job["status"] = "claimed"
            return dict(job)

        def report_work(self, job_code, *, action, **values):
            assert job_code == job["code"]
            work_reports.append({"action": action, **values})
            job["status"] = "in_progress"
            return dict(job)

    class ControlledCache(QualityCacheManager):
        def cache_job(self, client, claimed_job, *, progress_callback=None, episode_ready_callback=None):
            asset_root = self.cache_root / "ready" / claimed_job["code"] / claimed_job["asset_id"]
            state_path = asset_root.parent / ".qc-cache.json"
            state = {
                "schema_version": 3,
                "job_code": claimed_job["code"],
                "asset_id": claimed_job["asset_id"],
                "cache_complete": False,
                "cached_episode_count": 0,
                "total_episode_count": 2,
                "cached_bytes": 0,
                "total_bytes": 2,
                "episodes": [
                    {"episode_id": item["episode_id"], "relative_path": item["relative_path"], "status": "not_cached"}
                    for item in claimed_job["episodes"]
                ],
            }
            asset_root.mkdir(parents=True, exist_ok=True)

            def publish(index: int):
                source_file = source / claimed_job["episodes"][index]["relative_path"] / "motion.bvh"
                target = asset_root / claimed_job["episodes"][index]["relative_path"] / "motion.bvh"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source_file.read_bytes())
                state["episodes"][index]["status"] = "ready"
                state["cached_episode_count"] = index + 1
                state["cached_bytes"] = index + 1
                self._write_json_atomic(state_path, state)
                episode_ready_callback(
                    {
                        "cache_dir": str(asset_root),
                        "episode_id": claimed_job["episodes"][index]["episode_id"],
                        "cached_episode_count": index + 1,
                        "total_episode_count": 2,
                    }
                )
                self._preserve_local_episode_mappings(state_path, state)

            publish(0)
            first_ready.set()
            assert continue_cache.wait(timeout=3)
            publish(1)
            state["cache_complete"] = True
            self._write_json_atomic(state_path, state)
            return {
                "cache_dir": str(asset_root),
                "cache_complete": True,
                "cached_episode_count": 2,
                "total_episode_count": 2,
            }

    fake_client = FakeFlowClient()
    with running_server(tmp_path) as (server, base_url):
        controlled_cache = ControlledCache(
            server.application.paths.root / "platform-cache", reserve_bytes=0
        )
        server.application._flow_client = fake_client
        server.application._quality_cache_manager = lambda: controlled_cache

        status, accepted = request_json(
            f"{base_url}/api/platform/jobs/{job['code']}/claim", method="POST"
        )
        assert status == 202
        assert accepted["accepted"] is True
        assert first_ready.wait(timeout=3)

        status, platform = request_json(f"{base_url}/api/platform/jobs")
        assert status == 200
        visible = platform["jobs"][0]
        assert visible["local_task_id"]
        assert visible["local_caching"] is True
        assert visible["cached_episode_count"] == 1
        assert visible["total_episode_count"] == 2
        assert len(work_reports) == 1
        assert server.application.start_platform_job(job["code"])["started"] is False
        assert len(work_reports) == 1

        continue_cache.set()
        for _ in range(200):
            _status, platform = request_json(f"{base_url}/api/platform/jobs")
            if not platform["jobs"][0]["local_caching"]:
                break
            time.sleep(0.01)
        assert platform["jobs"][0]["cached_episode_count"] == 2


def test_platform_login_resumes_an_incomplete_episode_cache_after_restart(tmp_path: Path):
    job = {
        "code": "QCJ-RESUME-WEB",
        "status": "in_progress",
        "asset_id": "AST-RESUME-WEB",
        "episodes": [{"episode_id": "AST-RESUME-WEB-EP0001"}],
    }
    resumed = threading.Event()

    class FakeFlowClient:
        def jobs_response(self):
            return {"reviewer": "Web 质检员", "jobs": [dict(job)]}

    with running_server(tmp_path) as (server, base_url):
        state_path = (
            server.application.paths.root
            / "platform-cache"
            / "ready"
            / job["code"]
            / ".qc-cache.json"
        )
        QualityCacheManager._write_json_atomic(
            state_path,
            {
                "schema_version": 3,
                "job_code": job["code"],
                "asset_id": job["asset_id"],
                "cache_complete": False,
                "cached_episode_count": 1,
                "total_episode_count": 1,
                "cached_bytes": 1,
                "total_bytes": 2,
                "episodes": [
                    {
                        "episode_id": "AST-RESUME-WEB-EP0001",
                        "status": "ready",
                    }
                ],
            },
        )
        server.application._flow_client_factory = lambda *_args: FakeFlowClient()
        server.application._cache_platform_job = lambda _client, code: (
            resumed.set() if code == job["code"] else None
        )

        status, payload = request_json(
            f"{base_url}/api/platform/login",
            method="POST",
            payload={
                "baseUrl": "http://flow.test:8000",
                "username": "reviewer",
                "password": "secret",
            },
        )

        assert status == 200
        assert resumed.wait(timeout=3)
        assert payload["jobs"][0]["local_caching"] is True


def test_platform_login_replays_a_pending_final_cache_report(tmp_path: Path):
    job = {
        "code": "QCJ-PENDING-WEB-REPORT",
        "status": "in_progress",
        "asset_id": "AST-PENDING-WEB-REPORT",
    }

    class FakeFlowClient:
        def __init__(self):
            self.reports = []

        def jobs_response(self):
            return {"reviewer": "Web 质检员", "jobs": [dict(job)]}

        def report_cache(self, job_code, **values):
            self.reports.append((job_code, values))
            return dict(job)

    fake_client = FakeFlowClient()
    with running_server(tmp_path) as (server, base_url):
        state_path = (
            server.application.paths.root
            / "platform-cache"
            / "ready"
            / job["code"]
            / ".qc-cache.json"
        )
        QualityCacheManager._write_json_atomic(
            state_path,
            {
                "schema_version": 3,
                "job_code": job["code"],
                "asset_id": job["asset_id"],
                "cache_complete": True,
                "pending_cache_report": {
                    "status": "cache_ready",
                    "cache_progress": 100,
                    "cached_bytes": 9,
                    "cache_workstation": "QC-WS",
                },
            },
        )
        server.application._flow_client_factory = lambda *_args: fake_client
        status, _payload = request_json(
            f"{base_url}/api/platform/login",
            method="POST",
            payload={
                "baseUrl": "http://flow.test:8000",
                "username": "reviewer",
                "password": "secret",
            },
        )

        assert status == 200
        assert fake_client.reports == [
            (job["code"], {
                "status": "cache_ready",
                "cache_progress": 100,
                "cached_bytes": 9,
                "cache_workstation": "QC-WS",
            })
        ]
        assert "pending_cache_report" not in json.loads(
            state_path.read_text(encoding="utf-8")
        )


def test_web_claims_caches_and_submits_flow_job(tmp_path: Path):
    source = tmp_path / "nas" / "AST-WEB-001"
    episode_root = source / "episodes" / "episode_000001"
    episode_root.mkdir(parents=True)
    payload = b"""HIERARCHY
ROOT Hips
{
  OFFSET 0 0 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
  End Site
  {
    OFFSET 0 10 0
  }
}
MOTION
Frames: 2
Frame Time: 0.010000
0 0 0 0 0 0
1 0 0 0 0 0
"""
    primary = episode_root / "motion.bvh"
    primary.write_bytes(payload)
    (episode_root / "metadata.json").write_text(
        '{"schema_version": 1}', encoding="utf-8"
    )
    checksum = hashlib.sha256(payload).hexdigest()
    size_bytes = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
    job = {
        "code": "QCJ-WEB-001",
        "version": 1,
        "status": "pending",
        "asset_id": "AST-WEB-001",
        "asset_size_bytes": size_bytes,
        "asset_file_count": 2,
        "task_code": "TASK-WEB-001",
        "task_name": "Web Flow 领取测试",
        "collector": "采集测试员",
        "reviewer_name": "",
        "source_uri": str(source),
        "asset_nas_uri": str(source),
        "required_episode_count": 1,
        "episodes": [
            {
                "episode_id": "AST-WEB-001-EP0001",
                "relative_path": "episodes/episode_000001",
                "primary_file": "motion.bvh",
                "checksum_sha256": checksum,
            }
        ],
        "cache_progress": 0,
    }
    manifest = {
        "schema_version": 1,
        "asset_id": job["asset_id"],
        "episodes": [
            {
                **job["episodes"][0],
                "manifest": {
                    "schema_version": 1,
                    "files": [
                        {
                            "relative_path": "episodes/episode_000001/motion.bvh",
                            "size_bytes": primary.stat().st_size,
                            "sha256": checksum,
                        },
                        {
                            "relative_path": "episodes/episode_000001/metadata.json",
                            "size_bytes": (episode_root / "metadata.json").stat().st_size,
                            "sha256": hashlib.sha256(
                                (episode_root / "metadata.json").read_bytes()
                            ).hexdigest(),
                        },
                    ],
                },
            }
        ],
    }
    (source / "asset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    job["asset_manifest"] = manifest
    job["asset_manifest_sha256"] = canonical_json_sha256(manifest)
    job["result_upload_uri"] = str(
        tmp_path / "nas" / "qc-results" / job["asset_id"] / job["code"]
    )
    job["next_attempt"] = 1

    class FakeWebFlowClient:
        def __init__(self):
            self.progress_reports = []

        def jobs_response(self):
            return {"reviewer": "Web 质检员", "jobs": [dict(job)]}

        def jobs(self):
            return [dict(job)]

        def claim(self, job_code):
            assert job_code == job["code"]
            job.update(status="claimed", reviewer_name="Web 质检员")
            return dict(job)

        def report_cache(self, job_code, **values):
            assert job_code == job["code"]
            if job.get("status") == "in_progress" and values.get("status") in {
                "caching",
                "cache_ready",
            }:
                job.update({key: value for key, value in values.items() if key != "status"})
            else:
                job.update(values)
            return dict(job)

        def report_work(self, job_code, *, action, **values):
            assert job_code == job["code"]
            job.update(status="in_progress", **values)
            return {**job, "action": action}

        def report_review_progress(self, job_code, **values):
            assert job_code == job["code"]
            self.progress_reports.append(values)
            return {"job_code": job_code, **values}

        def submit_result(self, job_code, **values):
            assert job_code == job["code"]
            job.update(status="completed", submitted=values)
            return dict(job)

    fake_client = FakeWebFlowClient()
    with running_server(tmp_path) as (server, base_url):
        server.application._flow_client_factory = (
            lambda base_url, username, password: fake_client
        )
        server.application._quality_cache_manager = lambda: QualityCacheManager(
            server.application.paths.root / "platform-cache",
            reserve_bytes=0,
            workspace_name="QC-WEB-TEST",
        )

        status, login = request_json(
            f"{base_url}/api/platform/login",
            method="POST",
            payload={
                "baseUrl": "http://flow.test:8000",
                "username": "reviewer",
                "password": "secret",
            },
        )
        assert status == 200
        assert login["connected"] is True
        assert login["reviewer"] == "Web 质检员"
        assert "password" not in login

        status, accepted = request_json(
            f"{base_url}/api/platform/jobs/{job['code']}/claim",
            method="POST",
        )
        assert status == 202
        assert accepted["accepted"] is True

        local_task_id = None
        for _ in range(200):
            _status, platform = request_json(f"{base_url}/api/platform/jobs")
            visible_job = platform["jobs"][0]
            local_task_id = visible_job.get("local_task_id")
            if local_task_id and not visible_job["local_caching"]:
                break
            time.sleep(0.01)
        assert local_task_id
        assert job["status"] == "in_progress"
        assert visible_job["cached_episode_count"] == 1
        assert visible_job["total_episode_count"] == 1
        assert "已缓存 ${cachedEpisodes}/${totalEpisodes} Episode" in (
            Path(__file__).resolve().parents[1] / "app" / "renderer" / "renderer.js"
        ).read_text(encoding="utf-8")

        status, state = request_json(
            f"{base_url}/api/workspace?task_id={local_task_id}"
        )
        assert status == 200
        assert state["selected_task"]["origin"] == "flow"
        assert state["selected_task"]["flow_job_code"] == job["code"]
        local_episode_id = state["episodes"][0]["id"]

        status, reviewed = request_json(
            f"{base_url}/api/episodes/{local_episode_id}/review",
            method="POST",
            payload={
                "status": "completed",
                "decision": "pass",
                "reviewer": "Web 质检员",
            },
        )
        assert status == 200
        assert reviewed["review_status"] == "completed"
        for _ in range(200):
            if fake_client.progress_reports:
                break
            time.sleep(0.01)
        assert len(fake_client.progress_reports) == 1
        progress = fake_client.progress_reports[0]
        assert progress["review_started_at"]
        assert progress["review_completed_at"]
        assert progress["completed_episodes"] == [
            {
                "episode_id": job["episodes"][0]["episode_id"],
                "completed_at": reviewed["reviewed_at"],
            }
        ]

        status, submitted = request_json(
            f"{base_url}/api/platform/jobs/{job['code']}/submit",
            method="POST",
        )
        assert status == 200
        assert submitted["job"]["status"] == "completed"
        assert submitted["local_task"]["status"] == "submitted"
        assert job["submitted"]["review_started_at"] == progress["review_started_at"]
        assert job["submitted"]["review_completed_at"] == progress["review_completed_at"]
        assert (
            Path(job["result_upload_uri"])
            / "attempt-0001"
            / "qc_result.json"
        ).is_file()


def test_web_platform_refreshes_and_selects_reviewer_without_password(tmp_path: Path):
    class FakeReviewerClient:
        def __init__(self):
            self.selected = ""

        def reviewers(self):
            return {
                "date": "2026-08-05",
                "reviewers": [
                    {
                        "employee_no": "QC001",
                        "display_name": "Web 质检员",
                        "team_code": "QC",
                        "team_name": "质检组",
                    }
                ],
            }

        def login_reviewer(self, employee_no):
            self.selected = employee_no
            return {
                "token": "signed-reviewer-token",
                "token_type": "Bearer",
                "reviewer": {
                    "employee_no": employee_no,
                    "display_name": "Web 质检员",
                },
            }

        def jobs_response(self, statuses=None):
            assert self.selected == "QC001"
            return {"reviewer": "Web 质检员", "jobs": []}

    fake_client = FakeReviewerClient()
    with running_server(tmp_path) as (server, base_url):
        server.application._flow_client_factory = (
            lambda flow_url, username, password: fake_client
        )
        status, reviewers = request_json(
            f"{base_url}/api/platform/reviewers",
            method="POST",
            payload={"baseUrl": "http://flow.test:8000"},
        )
        assert status == 200
        assert reviewers["reviewers"][0]["employee_no"] == "QC001"
        status, login = request_json(
            f"{base_url}/api/platform/login",
            method="POST",
            payload={
                "baseUrl": "http://flow.test:8000",
                "employeeNo": "QC001",
            },
        )
        assert status == 200
        assert login["connected"] is True
        assert login["reviewer"] == "Web 质检员"
        assert login["employee_no"] == "QC001"


def test_web_manages_label_sets_and_clears_local_task_history(tmp_path: Path):
    bvh = """HIERARCHY
ROOT Hips
{
  OFFSET 0 0 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
  End Site
  {
    OFFSET 0 10 0
  }
}
MOTION
Frames: 2
Frame Time: 0.010000
0 0 0 0 0 0
1 0 0 0 0 0
"""
    sources = []
    for name in ("task-one", "task-two"):
        source = tmp_path / name
        episode = source / "episode_000001"
        episode.mkdir(parents=True)
        (episode / "motion.bvh").write_text(bvh, encoding="utf-8")
        sources.append(source)
    schema = {
        "schema": {
            "schema_type": "annotation_label_schema",
            "schema_version": "1.0.0",
            "label_set_id": "web_labels",
            "label_set_name": "Web 标签",
            "language": "zh-CN",
        },
        "severity_levels": [{"code": "normal", "name": "一般", "order": 1}],
        "actions": [{"code": "keep", "name": "保留"}],
        "groups": [{"code": "general", "name": "通用", "order": 1}],
        "labels": [
            {
                "code": "web_label",
                "name": "Web 标签",
                "group": "general",
                "enabled": True,
                "annotation_scopes": ["episode"],
                "target_types": ["global"],
                "default_severity": "normal",
                "default_action": "keep",
                "color": "#8844EE",
            }
        ],
    }
    label_paths = []
    for version in ("1.0.0", "2.0.0"):
        schema["schema"]["schema_version"] = version
        path = tmp_path / f"labels-{version}.json"
        path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        label_paths.append(path)

    with running_server(tmp_path) as (_server, base_url):
        task_results = []
        for source in sources:
            status, indexed = request_json(
                f"{base_url}/api/tasks/import",
                method="POST",
                payload={"rootPath": str(source)},
            )
            assert status == 200
            task_results.append(indexed)
        for label_path in label_paths:
            status, preview = request_json(
                f"{base_url}/api/label-schema/preview",
                method="POST",
                payload={"schemaPath": str(label_path)},
            )
            assert status == 200 and preview["readyToConfirm"]
            status, _ = request_json(
                f"{base_url}/api/label-schema/import", method="POST"
            )
            assert status == 200

        status, libraries = request_json(f"{base_url}/api/label-sets")
        assert status == 200
        assert len(libraries["label_sets"]) == 2
        inactive = next(item for item in libraries["label_sets"] if not item["active"])
        status, activated = request_json(
            f"{base_url}/api/label-sets/{inactive['id']}/activate",
            method="POST",
        )
        assert status == 200 and activated["active"]["id"] == inactive["id"]
        other = next(item for item in activated["label_sets"] if not item["active"])
        status, deleted = request_json(
            f"{base_url}/api/label-sets/{other['id']}", method="DELETE"
        )
        assert status == 200 and len(deleted["label_sets"]) == 1

        status, cleared = request_json(
            f"{base_url}/api/tasks/history?keep_task_id={task_results[0]['task_id']}",
            method="DELETE",
        )
        assert status == 200
        assert cleared["removed_count"] == 1
        assert cleared["removed_tasks"][0]["id"] == task_results[1]["task_id"]
        assert sources[1].is_dir()


def test_web_server_allows_declared_lan_hosts_and_origins_only(tmp_path: Path):
    lan_hosts = ("192.168.123.222", "10.1.11.155")
    with running_server(tmp_path, public_hosts=lan_hosts) as (server, _base_url):
        port = server.server_address[1]

        for lan_host in lan_hosts:
            connection = HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/", headers={"Host": f"{lan_host}:{port}"})
            response = connection.getresponse()
            assert response.status == 307
            assert response.getheader("Location") == f"/?token={TOKEN}"
            response.read()
            connection.close()

        body = json.dumps({"reviewer": "局域网测试员"}).encode("utf-8")
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "POST",
            "/api/workspace/settings",
            body=body,
            headers={
                "Host": f"10.1.11.155:{port}",
                "Origin": f"http://10.1.11.155:{port}",
                "X-Episode-QC-Token": TOKEN,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["reviewer_name"] == "局域网测试员"
        connection.close()

        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/", headers={"Host": f"10.1.99.99:{port}"})
        response = connection.getresponse()
        assert response.status == 401
        assert json.loads(response.read())["error"] == "拒绝未授权 Host"
        connection.close()

        body = json.dumps({"reviewer": "非法来源"}).encode("utf-8")
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "POST",
            "/api/workspace/settings",
            body=body,
            headers={
                "Host": f"10.1.11.155:{port}",
                "Origin": f"http://10.1.99.99:{port}",
                "X-Episode-QC-Token": TOKEN,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        assert response.status == 401
        assert json.loads(response.read())["error"] == "请求 Origin 无效"
        connection.close()


def test_web_server_requires_public_host_for_wildcard_bind(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    paths = WebPaths(
        root=tmp_path / "workspace",
        db_path=tmp_path / "workspace" / "workspace.db",
        cache_root=tmp_path / "workspace" / "cache",
        static_root=project_root / "app" / "renderer",
        default_profile=tmp_path / "missing-profile.yaml",
        default_label_schema=tmp_path / "missing-labels.yaml",
    )
    with pytest.raises(ValueError, match="public-host"):
        create_web_server(paths, host="0.0.0.0")


def test_standalone_mode_imports_local_task_without_flow(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "单机任务"
    episode_root = source_root / "episode_000001"
    episode_root.mkdir(parents=True)
    (episode_root / "motion.bvh").write_text(
        """HIERARCHY
ROOT Hips
{
  OFFSET 0 0 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
  End Site
  {
    OFFSET 0 10 0
  }
}
MOTION
Frames: 2
Frame Time: 0.010000
0 0 0 0 0 0
1 0 0 0 0 0
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EPISODE_QC_FLOW_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("EPISODE_QC_FLOW_USERNAME", "must-not-connect")
    monkeypatch.setenv("EPISODE_QC_FLOW_PASSWORD", "must-not-connect")

    with running_server(tmp_path, flow_enabled=False) as (server, base_url):
        assert server.application._flow_client is None
        status, platform = request_json(f"{base_url}/api/platform/jobs")
        assert status == 200
        assert platform == {"enabled": False, "connected": False, "jobs": []}

        with pytest.raises(HTTPError) as error:
            request_json(
                f"{base_url}/api/platform/reviewers",
                method="POST",
                payload={"baseUrl": "http://127.0.0.1:1"},
            )
        assert error.value.code == 400
        assert "单机模式" in json.loads(error.value.read())["error"]

        status, imported = request_json(
            f"{base_url}/api/tasks/import",
            method="POST",
            payload={"rootPath": str(source_root)},
        )
        assert status == 200
        assert imported["ready"] == 1
        assert imported["task"]["origin"] == "local"
        assert imported["task"]["source_type"] == "server_path"


def test_web_api_streams_cached_binary_frames(tmp_path: Path):
    with running_server(tmp_path) as (server, base_url):
        manifest_dir = server.application.paths.cache_root / "episodes" / EPISODE_ID / "fingerprint" / "full"
        (manifest_dir / "cameras").mkdir(parents=True)
        (manifest_dir / "mocap").mkdir()
        (manifest_dir / "robot_actions").mkdir()
        jpeg = b"\xff\xd8web-frame\xff\xd9"
        motion = struct.pack("<qq14f2B", -1, 7, *[float(index) for index in range(14)], 1, 0)
        action = struct.pack(
            "<qqI29f3f4f",
            123,
            8,
            2,
            *[index / 10 for index in range(29)],
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
        )
        (manifest_dir / "cameras" / "head.frames").write_bytes(jpeg)
        (manifest_dir / "mocap" / "motion.frames").write_bytes(motion)
        (manifest_dir / "robot_actions" / "policy.frames").write_bytes(action)
        manifest = {
            "cameras": [{
                "stream_id": STREAM_ID,
                "frames_file": "cameras/head.frames",
                "index": [[100, 0, len(jpeg), 0]],
            }],
            "motion": {
                "available": True,
                "frames_file": "mocap/motion.frames",
                "frame_encoding": MOTION_FRAME_ENCODING,
                "joint_names": ["Hips", "Head"],
                "index": [[110, 0, len(motion), 0]],
            },
            "robot_actions": {
                "joint_names": [],
                "sources": [{
                    "key": "policy",
                    "available": True,
                    "frames_file": "robot_actions/policy.frames",
                    "frame_encoding": ACTION_FRAME_ENCODING,
                    "index": [[120, 0, len(action), 0]],
                }],
            },
        }
        manifest_path = manifest_dir / "stream_index.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        server.application.playback.set(EPISODE_ID, {"manifest_path": str(manifest_path)})

        camera_request = Request(
            f"{base_url}/api/episodes/{EPISODE_ID}/cameras/{STREAM_ID}/frame?time_ns=100",
            headers={"X-Episode-QC-Token": TOKEN},
        )
        with LOCAL_OPENER.open(camera_request, timeout=5) as response:
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.headers["X-Frame-Index"] == "0"
            assert response.read() == jpeg

        motion_request = Request(
            f"{base_url}/api/episodes/{EPISODE_ID}/motion/frame?time_ns=110",
            headers={"X-Episode-QC-Token": TOKEN},
        )
        with LOCAL_OPENER.open(motion_request, timeout=5) as response:
            assert response.headers["Content-Type"] == "application/vnd.episode-qc.motion"
            assert response.read() == motion

        action_request = Request(
            f"{base_url}/api/episodes/{EPISODE_ID}/actions/policy/frame?time_ns=120",
            headers={"X-Episode-QC-Token": TOKEN},
        )
        with LOCAL_OPENER.open(action_request, timeout=5) as response:
            assert response.headers["Content-Type"] == "application/vnd.episode-qc.action"
            assert response.read() == action


def test_web_api_consumes_optional_post_body_on_persistent_connection(tmp_path: Path):
    with running_server(tmp_path) as (server, _base_url):
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        headers = {
            "X-Episode-QC-Token": TOKEN,
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        }
        connection.request("POST", "/api/undo", body="{}", headers=headers)
        first = connection.getresponse()
        assert first.status == 200
        assert json.loads(first.read()) is None

        connection.request("GET", "/api/workspace", headers={"X-Episode-QC-Token": TOKEN})
        second = connection.getresponse()
        assert second.status == 200
        assert json.loads(second.read())["workspace"]["name"] == "Mocap QC 工作区"
        connection.close()


def test_web_api_consumes_unauthorized_post_body_on_persistent_connection(tmp_path: Path):
    with running_server(tmp_path) as (server, _base_url):
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(
            "POST",
            "/api/undo",
            body='{"stale":true}',
            headers={
                "X-Episode-QC-Token": "stale-token",
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{server.server_address[1]}",
            },
        )
        first = connection.getresponse()
        assert first.status == 401
        assert json.loads(first.read())["error"] == "访问令牌无效"

        connection.request("GET", "/api/workspace", headers={"X-Episode-QC-Token": TOKEN})
        second = connection.getresponse()
        assert second.status == 200
        assert json.loads(second.read())["workspace"]["name"] == "Mocap QC 工作区"
        connection.close()


def test_web_token_persists_in_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace_root = tmp_path / "workspace"
    monkeypatch.delenv("EPISODE_QC_WEB_TOKEN", raising=False)

    first = persistent_web_token(workspace_root)
    second = persistent_web_token(workspace_root)

    assert first == second
    assert len(first) >= 32
    assert (workspace_root / ".web-token").read_text(encoding="utf-8").strip() == first
    assert (workspace_root / ".web-token").stat().st_mode & 0o777 == 0o600

    monkeypatch.setenv("EPISODE_QC_WEB_TOKEN", "configured-stable-token")
    assert persistent_web_token(workspace_root) == "configured-stable-token"
    assert (workspace_root / ".web-token").read_text(encoding="utf-8").strip() == "configured-stable-token"


def test_web_full_review_flow_exports_one_task_file_without_subdirectory(tmp_path: Path):
    source_root = tmp_path / "web-task"
    episode_root = source_root / "episode_000001"
    episode_root.mkdir(parents=True)
    (episode_root / "motion.bvh").write_text(
        """HIERARCHY
ROOT Hips
{
  OFFSET 0 0 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
  JOINT Head
  {
    OFFSET 0 100 0
    CHANNELS 3 Zrotation Yrotation Xrotation
    End Site
    {
      OFFSET 0 20 0
    }
  }
}
MOTION
Frames: 2
Frame Time: 0.010000
100 200 300 0 0 0 0 0 0
100 200 300 90 0 0 0 0 0
""",
        encoding="utf-8",
    )
    (episode_root / "metadata.json").write_text('{"episode_id":"WEB-EP-1"}\n', encoding="utf-8")
    labels_path = tmp_path / "web-labels.yaml"
    labels_path.write_text(
        """标签库名称: Web 全流程测试标签
版本: "1.0.0"
标签:
  - 编码: body_sway
    名称: 身体明显晃动
    范围: 区间
    对象: 动捕
    严重程度: 一般
    处理建议: 保留但标记
""",
        encoding="utf-8",
    )
    export_root = tmp_path / "exports"

    with running_server(tmp_path) as (_server, base_url):
        status, indexed = request_json(
            f"{base_url}/api/sources",
            method="POST",
            payload={"rootPath": str(source_root)},
        )
        assert status == 200
        assert indexed["discovered"] == 1
        assert indexed["ready"] == 1
        first_task_id = indexed["task_id"]

        status, tasks = request_json(f"{base_url}/api/tasks")
        assert status == 200
        assert len(tasks["tasks"]) == 1
        assert tasks["tasks"][0]["task_name"] == "web-task"

        status, preview = request_json(
            f"{base_url}/api/label-schema/preview",
            method="POST",
            payload={"schemaPath": str(labels_path)},
        )
        assert status == 200
        assert preview["readyToConfirm"] is True
        status, imported = request_json(f"{base_url}/api/label-schema/import", method="POST")
        assert status == 200
        assert imported["active"] is True

        status, workspace = request_json(f"{base_url}/api/workspace")
        assert status == 200
        episode_id = workspace["episodes"][0]["id"]
        status, detail = request_json(f"{base_url}/api/episodes/{episode_id}")
        assert status == 200
        assert detail["episode"]["duration_ns"] == 20_000_000

        status, priority_cache = request_json(
            f"{base_url}/api/episodes/{episode_id}/cache",
            method="POST",
        )
        assert status == 200
        assert priority_cache["cache_mode"] == "priority"

        motion_status = None
        for _ in range(50):
            motion_request = Request(
                f"{base_url}/api/episodes/{episode_id}/motion/frame?time_ns=10000000",
                headers={"X-Episode-QC-Token": TOKEN},
            )
            with LOCAL_OPENER.open(motion_request, timeout=5) as response:
                motion_status = response.status
                response.read()
            if motion_status == 200:
                break
            time.sleep(0.02)
        assert motion_status == 200

        annotation_payload = {
            "episode_id": episode_id,
            "label_code": "body_sway",
            "scope": "time_range",
            "start_offset_ns": 1_000_000,
            "end_offset_ns": 10_000_000,
            "target_type": "mocap",
            "target_key": "/mocap/human_motion",
            "severity": "normal",
            "action": "keep_with_label",
            "comment": "Web API 全流程测试",
            "attributes": {},
            "reviewer_name": "Web 测试员",
            "status": "confirmed",
        }
        status, annotation = request_json(
            f"{base_url}/api/annotations",
            method="POST",
            payload={"payload": annotation_payload},
        )
        assert status == 200
        assert annotation["label_code"] == "body_sway"

        status, undone = request_json(f"{base_url}/api/undo", method="POST")
        assert status == 200
        assert undone["operation"] == "undo"
        status, redone = request_json(f"{base_url}/api/redo", method="POST")
        assert status == 200
        assert redone["operation"] == "redo"

        status, reviewed = request_json(
            f"{base_url}/api/episodes/{episode_id}/review",
            method="POST",
            payload={
                "status": "completed",
                "decision": "pass_with_labels",
                "reviewer": "Web 测试员",
                "playheadNs": 10_000_000,
            },
        )
        assert status == 200
        assert reviewed["review_status"] == "completed"
        assert reviewed["quality_decision"] == "pass_with_labels"

        status, exported = request_json(
            f"{base_url}/api/export",
            method="POST",
            payload={
                "outputParent": str(export_root),
                "format": "json",
                "episodeIds": [episode_id],
            },
        )
        assert status == 200
        output_file = Path(exported["output_file"])
        assert output_file == export_root / "web-task_标注结果.json"
        assert output_file.is_file()
        assert [item.name for item in export_root.iterdir()] == ["web-task_标注结果.json"]
        assert not any(item.is_dir() for item in export_root.iterdir())
        document = json.loads(output_file.read_text(encoding="utf-8"))
        assert document["task_name"] == "web-task"
        assert document["episode_count"] == 1
        assert document["annotation_count"] == 1
        assert document["annotations"][0]["label_code"] == "body_sway"

        second_source = tmp_path / "web-task-two"
        second_episode = second_source / "episode_000002"
        second_episode.mkdir(parents=True)
        (second_episode / "motion.bvh").write_text(
            (episode_root / "motion.bvh").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        status, second_indexed = request_json(
            f"{base_url}/api/tasks/import",
            method="POST",
            payload={"rootPath": str(second_source)},
        )
        assert status == 200
        assert second_indexed["task_id"] != first_task_id

        status, first_workspace = request_json(
            f"{base_url}/api/workspace?task_id={first_task_id}"
        )
        assert status == 200
        assert len(first_workspace["episodes"]) == 1
        assert first_workspace["episodes"][0]["task_id"] == first_task_id

        second_task_id = second_indexed["task_id"]
        status, second_workspace = request_json(
            f"{base_url}/api/workspace?task_id={second_task_id}"
        )
        assert status == 200
        assert len(second_workspace["episodes"]) == 1
        assert second_workspace["episodes"][0]["task_id"] == second_task_id

        status, rescanned = request_json(
            f"{base_url}/api/tasks/{second_task_id}/rescan",
            method="POST",
        )
        assert status == 200
        assert rescanned["existing_task"] is True
        assert rescanned["task_id"] == second_task_id
