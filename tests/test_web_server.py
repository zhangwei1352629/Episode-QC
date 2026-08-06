from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import struct
import threading
import time
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

import pytest

from episode_qc.playback import ACTION_FRAME_ENCODING, MOTION_FRAME_ENCODING
from episode_qc.platform_workflow import QualityCacheManager, canonical_json_sha256
from episode_qc.web_server import (
    WebPaths,
    create_web_server,
    persistent_web_token,
)


TOKEN = "web-test-token"
EPISODE_ID = "ep_" + "a" * 24
STREAM_ID = "str_" + "b" * 24
LOCAL_OPENER = build_opener(ProxyHandler({}))


@contextmanager
def running_server(
    tmp_path: Path,
    *,
    public_hosts: tuple[str, ...] = (),
    flow_enabled: bool = True,
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
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


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
            assert b"Episode QC" in response.read()

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

        with LOCAL_OPENER.open(f"{base_url}/label-template-simple.yaml", timeout=5) as response:
            assert response.status == 200
            template = response.read().decode("utf-8")
            assert "标签库名称" in template
            assert "编码和名称必填" in template

        with LOCAL_OPENER.open(f"{base_url}/target-selection.mjs", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/javascript")
            assert b"resolveSelectedTarget" in response.read()


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
            job.update(values)
            return dict(job)

        def report_work(self, job_code, *, action, **values):
            assert job_code == job["code"]
            job.update(status="in_progress", **values)
            return {**job, "action": action}

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

        status, submitted = request_json(
            f"{base_url}/api/platform/jobs/{job['code']}/submit",
            method="POST",
        )
        assert status == 200
        assert submitted["job"]["status"] == "completed"
        assert submitted["local_task"]["status"] == "submitted"
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
