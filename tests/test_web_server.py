from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPConnection
import json
from pathlib import Path
import struct
import threading
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

import pytest

from episode_qc.playback import ACTION_FRAME_ENCODING, MOTION_FRAME_ENCODING
from episode_qc.web_server import WebPaths, create_web_server


TOKEN = "web-test-token"
EPISODE_ID = "ep_" + "a" * 24
STREAM_ID = "str_" + "b" * 24
LOCAL_OPENER = build_opener(ProxyHandler({}))


@contextmanager
def running_server(tmp_path: Path):
    project_root = Path(__file__).resolve().parents[1]
    workspace_root = tmp_path / "workspace"
    paths = WebPaths(
        root=workspace_root,
        db_path=workspace_root / "workspace.db",
        cache_root=workspace_root / "cache",
        static_root=project_root / "app" / "renderer",
        default_profile=tmp_path / "missing-profile.yaml",
        default_label_schema=tmp_path / "missing-labels.yaml",
    )
    server = create_web_server(paths, token=TOKEN)
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
    with running_server(tmp_path) as (_server, base_url):
        with pytest.raises(HTTPError) as error:
            LOCAL_OPENER.open(f"{base_url}/api/workspace", timeout=5)
        assert error.value.code == 401
        assert json.loads(error.value.read())["error"] == "访问令牌无效"

        status, state = request_json(f"{base_url}/api/workspace")
        assert status == 200
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


def test_web_api_streams_cached_binary_frames(tmp_path: Path):
    with running_server(tmp_path) as (server, base_url):
        manifest_dir = server.application.paths.cache_root / "episodes" / EPISODE_ID / "fingerprint" / "full"
        (manifest_dir / "cameras").mkdir(parents=True)
        (manifest_dir / "mocap").mkdir()
        (manifest_dir / "robot_actions").mkdir()
        jpeg = b"\xff\xd8web-frame\xff\xd9"
        motion = struct.pack("<qq14f2B", -1, 7, *[float(index) for index in range(14)], 1, 0)
        action = struct.pack("<qq29f", 123, 8, *[index / 10 for index in range(29)])
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
