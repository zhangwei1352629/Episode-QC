from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import threading
import traceback
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit
import webbrowser

from episode_qc.playback import (
    ACTION_FRAME_ENCODING,
    MOTION_FRAME_ENCODING,
    prepare_episode_cache,
    public_cache_manifest,
)
from episode_qc.workspace import (
    delete_annotation,
    episode_detail,
    export_workspace,
    import_label_schema,
    initialize_workspace,
    list_qc_tasks,
    preview_label_schema,
    qc_task_manifest,
    register_worker_task,
    redo_annotation_change,
    rescan_qc_task,
    save_annotation,
    scan_data_source,
    undo_annotation_change,
    update_episode_review,
    update_workspace_settings,
    WorkspaceConflictError,
    workspace_state,
)


ENTITY_ID = re.compile(r"^(?:ep|str|ann)_[a-f0-9]{24,32}$")
ACTION_KEYS = {"policy", "policy_target", "soma"}
WEB_TOKEN_FILE = ".web-token"
WORKER_ID_FILE = ".worker-id"
LOCAL_WEB_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WILDCARD_WEB_HOSTS = frozenset({"0.0.0.0", "::"})


@dataclass(frozen=True)
class WebPaths:
    root: Path
    db_path: Path
    cache_root: Path
    static_root: Path
    default_profile: Path
    default_label_schema: Path


def default_web_paths(workspace_root: str | Path | None = None) -> WebPaths:
    project_root = Path(__file__).resolve().parents[2]
    package_root = Path(__file__).resolve().parent
    packaged_static = package_root / "web_static"
    packaged_defaults = package_root / "defaults"
    if workspace_root is not None:
        root = Path(workspace_root).expanduser().resolve()
    elif os.environ.get("EPISODE_QC_WORKSPACE_ROOT"):
        root = Path(os.environ["EPISODE_QC_WORKSPACE_ROOT"]).expanduser().resolve()
    else:
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        root = (config_root / "episode-qc" / "workspaces" / "default").resolve()
    return WebPaths(
        root=root,
        db_path=root / "workspace.db",
        cache_root=root / "cache",
        static_root=packaged_static if packaged_static.is_dir() else project_root / "app" / "renderer",
        default_profile=(
            packaged_defaults / "data_profile_v1.example.yaml"
            if packaged_defaults.is_dir()
            else project_root / "mocap_qc_v1_design_bundle" / "data_profile_v1.example.yaml"
        ),
        default_label_schema=(
            packaged_static / "label-template-simple.yaml"
            if packaged_static.is_dir()
            else project_root / "app" / "renderer" / "label-template-simple.yaml"
        ),
    )


def persistent_web_token(workspace_root: Path) -> str:
    """Return a stable local token so open QC pages survive server restarts."""
    workspace_root.mkdir(parents=True, exist_ok=True)
    token_path = workspace_root / WEB_TOKEN_FILE
    configured = os.environ.get("EPISODE_QC_WEB_TOKEN", "").strip()
    if configured:
        token = configured
    elif token_path.is_file():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
        token = secrets.token_urlsafe(32)
    else:
        token = secrets.token_urlsafe(32)
    token_path.write_text(f"{token}\n", encoding="utf-8")
    token_path.chmod(0o600)
    return token


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[queue.Queue[dict[str, object]]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[dict[str, object]]:
        events: queue.Queue[dict[str, object]] = queue.Queue(maxsize=16)
        with self._lock:
            self._subscribers.add(events)
        return events

    def unsubscribe(self, events: queue.Queue[dict[str, object]]) -> None:
        with self._lock:
            self._subscribers.discard(events)

    def publish(self, payload: dict[str, object]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for events in subscribers:
            try:
                events.put_nowait(payload)
            except queue.Full:
                try:
                    events.get_nowait()
                except queue.Empty:
                    pass
                try:
                    events.put_nowait(payload)
                except queue.Full:
                    pass


class PlaybackRegistry:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root.resolve()
        self._items: dict[str, tuple[Path, dict[str, object]]] = {}
        self._lock = threading.Lock()

    def set(self, episode_id: str, result: dict[str, object]) -> None:
        manifest_path = Path(str(result["manifest_path"])).resolve()
        if not _is_relative_to(manifest_path, self.cache_root) or manifest_path.name != "stream_index.json":
            raise ValueError("播放缓存路径超出工作区")
        manifest = result if _manifest_has_indices(result) else json.loads(manifest_path.read_text(encoding="utf-8"))
        with self._lock:
            self._items[episode_id] = (manifest_path, manifest)

    def camera_frame(self, episode_id: str, stream_id: str, time_ns: int) -> tuple[bytes, dict[str, int | bool]]:
        manifest_path, manifest = self._get(episode_id)
        camera = next((item for item in manifest.get("cameras", []) if item.get("stream_id") == stream_id), None)
        if not camera or not camera.get("index"):
            raise KeyError(f"相机缓存不存在: {stream_id}")
        entry = _nearest_entry(camera["index"], time_ns)
        payload = _read_frame_slice(manifest_path, str(camera["frames_file"]), entry)
        return payload, _frame_metadata(entry, time_ns, len(camera["index"]))

    def motion_frame(self, episode_id: str, time_ns: int) -> tuple[bytes, dict[str, int | bool]] | None:
        manifest_path, manifest = self._get(episode_id)
        motion = manifest.get("motion") or {}
        if not motion.get("available") or not motion.get("index"):
            return None
        if motion.get("frame_encoding") != MOTION_FRAME_ENCODING:
            raise ValueError("Mocap 缓存不是浏览器支持的二进制版本，请重新生成缓存")
        entry = _nearest_entry(motion["index"], time_ns)
        payload = _read_frame_slice(manifest_path, str(motion["frames_file"]), entry)
        return payload, _frame_metadata(entry, time_ns, len(motion["index"]))

    def action_frame(self, episode_id: str, source_key: str, time_ns: int) -> tuple[bytes, dict[str, int | bool]] | None:
        manifest_path, manifest = self._get(episode_id)
        actions = manifest.get("robot_actions") or {}
        source = next((item for item in actions.get("sources", []) if item.get("key") == source_key), None)
        if not source or not source.get("available") or not source.get("index"):
            return None
        if source.get("frame_encoding") != ACTION_FRAME_ENCODING:
            raise ValueError("机器人动作缓存不是浏览器支持的二进制版本，请重新生成缓存")
        entry = _nearest_entry(source["index"], time_ns)
        payload = _read_frame_slice(manifest_path, str(source["frames_file"]), entry)
        return payload, _frame_metadata(entry, time_ns, len(source["index"]))

    def _get(self, episode_id: str) -> tuple[Path, dict[str, object]]:
        with self._lock:
            value = self._items.get(episode_id)
        if value is None:
            raise KeyError("请先准备 Episode 播放缓存")
        return value


class EpisodeQcWebApplication:
    def __init__(
        self,
        paths: WebPaths,
        *,
        token: str | None = None,
        worker_info: dict[str, str] | None = None,
    ) -> None:
        self.paths = paths
        self.token = token or secrets.token_urlsafe(32)
        self.events = EventHub()
        self.playback = PlaybackRegistry(paths.cache_root)
        self.pending_label_schema: Path | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="episode-qc-cache")
        self._jobs: set[str] = set()
        self._jobs_lock = threading.Lock()
        self.session_id = f"web-{self.token[:12]}"
        self.worker_info = worker_info
        paths.root.mkdir(parents=True, exist_ok=True)
        initialize_workspace(paths.db_path)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def get_workspace_state(self, task_id: str | None = None) -> dict[str, object]:
        if task_id is None:
            tasks = list_qc_tasks(self.paths.db_path)
            task_id = str(tasks[0]["id"]) if tasks else None
        state = workspace_state(self.paths.db_path, task_id=task_id)
        if not state.get("label_schema") and self.paths.default_label_schema.is_file():
            import_label_schema(self.paths.db_path, self.paths.default_label_schema)
            state = workspace_state(self.paths.db_path, task_id=task_id)
        return state

    def update_settings(self, request: dict[str, object]) -> dict[str, object]:
        return update_workspace_settings(
            self.paths.db_path,
            name=request.get("name") if isinstance(request.get("name"), str) else None,
            reviewer_name=request.get("reviewer") if isinstance(request.get("reviewer"), str) else None,
            last_episode_id=request.get("lastEpisodeId") if isinstance(request.get("lastEpisodeId"), str) else None,
            task_id=request.get("taskId") if isinstance(request.get("taskId"), str) else None,
        )

    def get_tasks(self) -> dict[str, object]:
        return {"tasks": list_qc_tasks(self.paths.db_path)}

    def get_worker_info(self) -> dict[str, object]:
        if self.worker_info is None:
            raise KeyError("当前服务不是客户端 Data Worker")
        return {"worker": self.worker_info}

    def get_task_manifest(self, task_id: str) -> dict[str, object]:
        if self.worker_info is None:
            raise KeyError("任务清单接口只在客户端 Data Worker 中开放")
        return qc_task_manifest(self.paths.db_path, task_id)

    def register_worker_source(self, request: dict[str, object]) -> dict[str, object]:
        if self.worker_info is not None:
            raise ValueError("Data Worker 不能作为中央 QC 服务注册其他 Worker")
        worker = request.get("worker")
        manifest = request.get("manifest")
        if not isinstance(worker, dict) or not isinstance(manifest, dict):
            raise ValueError("客户端 Worker 注册信息无效")
        return register_worker_task(self.paths.db_path, worker=worker, manifest=manifest)

    def add_source(self, request: dict[str, object]) -> dict[str, object]:
        root_path = request.get("rootPath")
        if not isinstance(root_path, str) or not root_path.strip():
            raise ValueError("请输入数据源目录")
        profile_path = self.paths.default_profile if self.paths.default_profile.is_file() else None
        return scan_data_source(self.paths.db_path, root_path, profile_path=profile_path)

    def rescan_task(self, task_id: str) -> dict[str, object]:
        profile_path = self.paths.default_profile if self.paths.default_profile.is_file() else None
        return rescan_qc_task(self.paths.db_path, task_id, profile_path=profile_path)

    def prepare_episode(self, episode_id: str) -> dict[str, object]:
        detail = episode_detail(self.paths.db_path, episode_id)
        if detail["episode"].get("source_type") == "client_worker":
            raise ValueError("此任务的数据在客户端电脑上，请启动该电脑的 Data Worker 后播放")
        result = prepare_episode_cache(
            self.paths.db_path,
            episode_id,
            self.paths.cache_root,
            mode="priority",
        )
        self.playback.set(episode_id, result)
        if not result.get("complete"):
            self._queue_full_cache(episode_id)
        return public_cache_manifest(result)

    def preview_labels(self, request: dict[str, object]) -> dict[str, object]:
        schema_path = request.get("schemaPath")
        if not isinstance(schema_path, str) or not schema_path.strip():
            raise ValueError("请输入标签库文件路径")
        path = Path(schema_path).expanduser().resolve()
        preview = preview_label_schema(self.paths.db_path, path)
        self.pending_label_schema = path if preview.get("valid") else None
        return {"imported": False, "readyToConfirm": bool(preview.get("valid")), "preview": preview}

    def import_pending_labels(self) -> dict[str, object]:
        if self.pending_label_schema is None:
            raise ValueError("没有待确认的标签库导入")
        path = self.pending_label_schema
        self.pending_label_schema = None
        return import_label_schema(self.paths.db_path, path)

    def save_annotation(self, request: dict[str, object]) -> dict[str, object]:
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("标注 payload 必须是对象")
        annotation_id = request.get("annotationId")
        if annotation_id is not None and not isinstance(annotation_id, str):
            raise ValueError("标注 ID 无效")
        return save_annotation(
            self.paths.db_path,
            payload,
            annotation_id=annotation_id,
            session_id=self.session_id,
            expected_updated_at=(
                payload.get("updated_at") if annotation_id and isinstance(payload.get("updated_at"), str) else None
            ),
        )

    def update_review(self, episode_id: str, request: dict[str, object]) -> dict[str, object]:
        playhead = request.get("playheadNs")
        return update_episode_review(
            self.paths.db_path,
            episode_id,
            review_status=request.get("status") if isinstance(request.get("status"), str) else None,
            quality_decision=request.get("decision") if isinstance(request.get("decision"), str) else None,
            reviewer_name=request.get("reviewer") if isinstance(request.get("reviewer"), str) else None,
            last_playhead_ns=round(float(playhead)) if isinstance(playhead, (int, float)) else None,
        )

    def export(self, request: dict[str, object]) -> dict[str, object]:
        output_parent = request.get("outputParent") or os.environ.get("EPISODE_QC_EXPORT_ROOT")
        if not isinstance(output_parent, str) or not output_parent.strip():
            raise ValueError("请输入标注结果保存目录")
        episode_ids = request.get("episodeIds") or []
        if not isinstance(episode_ids, list) or not all(isinstance(item, str) for item in episode_ids):
            raise ValueError("Episode ID 列表无效")
        return export_workspace(
            self.paths.db_path,
            output_parent,
            episode_ids=episode_ids,
            completed_only=bool(request.get("completedOnly")),
            export_format=str(request.get("format") or "json"),
            task_id=request.get("taskId") if isinstance(request.get("taskId"), str) else None,
        )

    def _queue_full_cache(self, episode_id: str) -> None:
        with self._jobs_lock:
            if episode_id in self._jobs:
                return
            self._jobs.add(episode_id)
        self._executor.submit(self._prepare_full_cache, episode_id)

    def _prepare_full_cache(self, episode_id: str) -> None:
        try:
            result = prepare_episode_cache(
                self.paths.db_path,
                episode_id,
                self.paths.cache_root,
                mode="full",
            )
            self.playback.set(episode_id, result)
            self.events.publish({"episodeId": episode_id, "cache": public_cache_manifest(result)})
        except Exception as exc:
            self.events.publish({"episodeId": episode_id, "error": str(exc)})
        finally:
            with self._jobs_lock:
                self._jobs.discard(episode_id)


class EpisodeQcWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        application: EpisodeQcWebApplication,
        *,
        allowed_hosts: frozenset[str],
        public_hosts: tuple[str, ...],
        cors_origins: frozenset[str],
    ) -> None:
        self.application = application
        self.allowed_hosts = allowed_hosts
        self.public_hosts = public_hosts
        self.cors_origins = cors_origins
        super().__init__(server_address, EpisodeQcRequestHandler)

    @property
    def allowed_origins(self) -> frozenset[str]:
        port = self.server_address[1]
        return frozenset(_http_origin(host, port) for host in self.allowed_hosts)

    @property
    def api_origins(self) -> frozenset[str]:
        return self.allowed_origins | self.cors_origins

    def server_close(self) -> None:
        self.application.close()
        super().server_close()


class EpisodeQcRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "EpisodeQC/1"

    @property
    def application(self) -> EpisodeQcWebApplication:
        return self.server.application  # type: ignore[attr-defined,no-any-return]

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            self._assert_allowed_host()
            origin = self.headers.get("Origin", "")
            if origin not in self.server.cors_origins:  # type: ignore[attr-defined]
                raise PermissionError("跨域来源未授权")
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Episode-QC-Token")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)

    def log_message(self, format_string: str, *args: object) -> None:
        if self.path.startswith("/api/") and not self.path.startswith("/api/events"):
            super().log_message(format_string, *args)

    def _dispatch(self, method: str) -> None:
        try:
            parsed = urlsplit(self.path)
            self._assert_allowed_host()
            if parsed.path.startswith("/api/"):
                self._assert_api_access(parsed)
                self._route_api(method, parsed.path, parse_qs(parsed.query))
            elif method == "GET":
                if not self._redirect_entry_with_current_token(parsed):
                    self._serve_static(parsed.path)
            else:
                self._send_json({"error": "方法不允许"}, HTTPStatus.METHOD_NOT_ALLOWED)
        except BrokenPipeError:
            pass
        except PermissionError as exc:
            if method in {"POST", "DELETE"}:
                try:
                    self._discard_body()
                except (OSError, ValueError):
                    self.close_connection = True
            self._send_json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except WorkspaceConflictError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except KeyError as exc:
            self._send_json({"error": str(exc).strip("'")}, HTTPStatus.NOT_FOUND)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _assert_api_access(self, parsed: Any) -> None:
        query_token = parse_qs(parsed.query).get("token", [""])[0]
        supplied = self.headers.get("X-Episode-QC-Token", "") or query_token
        if not hmac.compare_digest(supplied, self.application.token):
            raise PermissionError("访问令牌无效")
        origin = self.headers.get("Origin")
        if self.command != "GET" and origin:
            if origin not in self.server.api_origins:  # type: ignore[attr-defined]
                raise PermissionError("请求 Origin 无效")

    def _assert_allowed_host(self) -> None:
        raw_host = self.headers.get("Host", "")
        try:
            parsed = urlsplit(f"//{raw_host}")
            host = _normalize_web_host(parsed.hostname or "")
            _ = parsed.port
        except ValueError as exc:
            raise PermissionError("请求 Host 无效") from exc
        if (
            parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or host not in self.server.allowed_hosts  # type: ignore[attr-defined]
        ):
            raise PermissionError("拒绝未授权 Host")

    def _redirect_entry_with_current_token(self, parsed: Any) -> bool:
        if parsed.path not in {"", "/"}:
            return False
        supplied = parse_qs(parsed.query).get("token", [""])[0]
        if hmac.compare_digest(supplied, self.application.token):
            return False
        location = f"/?token={quote(self.application.token, safe='')}"
        self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return True

    def _route_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        app = self.application
        if method == "GET" and path == "/api/health":
            self._send_json({"ok": True})
            return
        if method == "GET" and path == "/api/workspace":
            task_id = query.get("task_id", [None])[0]
            self._send_json(app.get_workspace_state(task_id))
            return
        if method == "POST" and path == "/api/workspace/settings":
            self._send_json(app.update_settings(self._json_body()))
            return
        if method == "GET" and path == "/api/tasks":
            self._send_json(app.get_tasks())
            return
        if method == "GET" and path == "/api/worker/info":
            self._send_json(app.get_worker_info())
            return
        if method == "POST" and path == "/api/tasks/register-worker":
            self._send_json(app.register_worker_source(self._json_body()))
            return
        if method == "POST" and path in {"/api/sources", "/api/tasks/import"}:
            self._send_json(app.add_source(self._json_body()))
            return
        task_rescan_match = re.fullmatch(r"/api/tasks/(tsk_[a-f0-9]{24,32})/rescan", path)
        if method == "POST" and task_rescan_match:
            self._discard_body()
            self._send_json(app.rescan_task(task_rescan_match.group(1)))
            return
        task_manifest_match = re.fullmatch(r"/api/tasks/(tsk_[a-f0-9]{24,32})/manifest", path)
        if method == "GET" and task_manifest_match:
            self._send_json(app.get_task_manifest(task_manifest_match.group(1)))
            return
        if method == "GET" and path == "/api/events":
            self._serve_events()
            return
        if method == "POST" and path == "/api/label-schema/preview":
            self._send_json(app.preview_labels(self._json_body()))
            return
        if method == "POST" and path == "/api/label-schema/import":
            self._discard_body()
            self._send_json(app.import_pending_labels())
            return
        if method == "POST" and path == "/api/annotations":
            self._send_json(app.save_annotation(self._json_body()))
            return
        if method == "POST" and path == "/api/undo":
            self._discard_body()
            self._send_json(undo_annotation_change(app.paths.db_path, session_id=app.session_id))
            return
        if method == "POST" and path == "/api/redo":
            self._discard_body()
            self._send_json(redo_annotation_change(app.paths.db_path, session_id=app.session_id))
            return
        if method == "POST" and path == "/api/export":
            self._send_json(app.export(self._json_body()))
            return

        episode_match = re.fullmatch(r"/api/episodes/(ep_[a-f0-9]{24,32})", path)
        if method == "GET" and episode_match:
            self._send_json(episode_detail(app.paths.db_path, episode_match.group(1)))
            return
        cache_match = re.fullmatch(r"/api/episodes/(ep_[a-f0-9]{24,32})/cache", path)
        if method == "POST" and cache_match:
            self._discard_body()
            self._send_json(app.prepare_episode(cache_match.group(1)))
            return
        review_match = re.fullmatch(r"/api/episodes/(ep_[a-f0-9]{24,32})/review", path)
        if method == "POST" and review_match:
            self._send_json(app.update_review(review_match.group(1), self._json_body()))
            return
        camera_match = re.fullmatch(
            r"/api/episodes/(ep_[a-f0-9]{24,32})/cameras/(str_[a-f0-9]{24,32})/frame",
            path,
        )
        if method == "GET" and camera_match:
            payload, metadata = app.playback.camera_frame(
                camera_match.group(1), camera_match.group(2), _time_ns(query)
            )
            self._send_binary(payload, "image/jpeg", metadata)
            return
        motion_match = re.fullmatch(r"/api/episodes/(ep_[a-f0-9]{24,32})/motion/frame", path)
        if method == "GET" and motion_match:
            frame = app.playback.motion_frame(motion_match.group(1), _time_ns(query))
            if frame is None:
                self._send_empty(HTTPStatus.NO_CONTENT)
            else:
                self._send_binary(frame[0], "application/vnd.episode-qc.motion", frame[1])
            return
        action_match = re.fullmatch(
            r"/api/episodes/(ep_[a-f0-9]{24,32})/actions/(policy|policy_target|soma)/frame",
            path,
        )
        if method == "GET" and action_match:
            frame = app.playback.action_frame(action_match.group(1), action_match.group(2), _time_ns(query))
            if frame is None:
                self._send_empty(HTTPStatus.NO_CONTENT)
            else:
                self._send_binary(frame[0], "application/vnd.episode-qc.action", frame[1])
            return
        annotation_match = re.fullmatch(r"/api/annotations/(ann_[a-f0-9]{24,32})", path)
        if method == "DELETE" and annotation_match:
            self._send_json(
                delete_annotation(app.paths.db_path, annotation_match.group(1), session_id=app.session_id)
            )
            return
        self._send_json({"error": "API 不存在"}, HTTPStatus.NOT_FOUND)

    def _serve_events(self) -> None:
        events = self.application.events.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self._send_cors_headers()
            self.end_headers()
            while True:
                try:
                    payload = events.get(timeout=15)
                    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    self.wfile.write(b"data: " + data + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.application.events.unsubscribe(events)

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else unquote(request_path).lstrip("/")
        static_root = self.application.paths.static_root.resolve()
        path = (static_root / relative).resolve()
        if not _is_relative_to(path, static_root) or not path.is_file():
            self._send_json({"error": "静态资源不存在"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".mjs":
            content_type = "text/javascript"
        payload = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache" if path.suffix in {".html", ".js", ".mjs", ".css"} else "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 2_000_000:
            raise ValueError("请求体过大")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    def _discard_body(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 2_000_000:
            raise ValueError("请求体过大")
        if length:
            self.rfile.read(length)

    def _send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_binary(self, payload: bytes, content_type: str, metadata: dict[str, int | bool]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Offset-Ns", str(metadata["frame_offset_ns"]))
        self.send_header("X-Frame-Skew-Ns", str(metadata["skew_ns"]))
        self.send_header("X-Frame-Index", str(metadata["frame_index"]))
        self.send_header("X-End-Of-Stream", "1" if metadata["end_of_stream"] else "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and origin in self.server.cors_origins:  # type: ignore[attr-defined]
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header(
                "Access-Control-Expose-Headers",
                "X-Frame-Offset-Ns, X-Frame-Skew-Ns, X-Frame-Index, X-End-Of-Stream",
            )
            self.send_header("Vary", "Origin")


def create_web_server(
    paths: WebPaths,
    *,
    port: int = 0,
    token: str | None = None,
    host: str = "127.0.0.1",
    public_hosts: tuple[str, ...] = (),
    cors_origins: tuple[str, ...] = (),
    worker_info: dict[str, str] | None = None,
) -> EpisodeQcWebServer:
    if not 0 <= port <= 65535:
        raise ValueError("端口必须在 0 到 65535 之间")
    if not paths.static_root.is_dir():
        raise FileNotFoundError(f"Web 静态资源目录不存在: {paths.static_root}")
    bind_host = _normalize_web_host(host)
    normalized_public_hosts = tuple(dict.fromkeys(_normalize_web_host(item) for item in public_hosts))
    if bind_host in WILDCARD_WEB_HOSTS and not normalized_public_hosts:
        raise ValueError("监听全部网卡时至少需要一个 --public-host")
    if any(item in WILDCARD_WEB_HOSTS for item in normalized_public_hosts):
        raise ValueError("--public-host 必须是可访问的具体 IP 地址或主机名")
    advertised_hosts = normalized_public_hosts or (bind_host,)
    allowed_hosts = frozenset(LOCAL_WEB_HOSTS | set(advertised_hosts))
    normalized_cors_origins = frozenset(_normalize_http_origin(item) for item in cors_origins)
    application = EpisodeQcWebApplication(paths, token=token, worker_info=worker_info)
    return EpisodeQcWebServer(
        (bind_host, port),
        application,
        allowed_hosts=allowed_hosts,
        public_hosts=advertised_hosts,
        cors_origins=normalized_cors_origins,
    )


def serve_web_app(
    *,
    port: int = 0,
    workspace_root: str | Path | None = None,
    open_browser: bool = True,
    host: str = "127.0.0.1",
    public_hosts: tuple[str, ...] = (),
    cors_origins: tuple[str, ...] = (),
    worker_info: dict[str, str] | None = None,
    print_token: bool = False,
) -> None:
    paths = default_web_paths(workspace_root)
    server = create_web_server(
        paths,
        port=port,
        token=persistent_web_token(paths.root),
        host=host,
        public_hosts=public_hosts,
        cors_origins=cors_origins,
        worker_info=worker_info,
    )
    actual_port = server.server_address[1]
    entry_urls = [f"{_http_origin(item, actual_port)}/" for item in server.public_hosts]
    browser_url = f"{entry_urls[0]}?token={quote(server.application.token, safe='')}"
    print("Episode QC Web:", flush=True)
    for entry_url in entry_urls:
        print(f"  {entry_url}", flush=True)
    if any(item not in LOCAL_WEB_HOSTS for item in server.public_hosts):
        print("警告：局域网直达模式已开启；同网段用户打开上述地址后将获得完整质检权限。", flush=True)
    if print_token:
        print(f"访问令牌：{server.application.token}", flush=True)
    if open_browser:
        threading.Timer(0.25, webbrowser.open, args=(browser_url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _normalize_web_host(value: str) -> str:
    candidate = str(value).strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if not candidate or any(character.isspace() for character in candidate):
        raise ValueError("主机地址不能为空或包含空白")
    try:
        return ipaddress.ip_address(candidate).compressed.lower()
    except ValueError:
        if ":" in candidate or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", candidate):
            raise ValueError(f"主机地址无效: {value}") from None
        return candidate.rstrip(".").lower()


def _http_origin(host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host else host
    return f"http://{rendered_host}:{port}"


def _normalize_http_origin(value: str) -> str:
    candidate = str(value).strip().rstrip("/")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Origin 地址无效: {value}")
    try:
        if parsed.port is None:
            raise ValueError
    except ValueError:
        raise ValueError(f"Origin 地址必须包含有效端口: {value}") from None
    return candidate


def persistent_worker_identity(workspace_root: str | Path) -> dict[str, str]:
    root = Path(workspace_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / WORKER_ID_FILE
    worker_id = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    if not re.fullmatch(r"wrk_[a-f0-9]{24,32}", worker_id):
        worker_id = _worker_id()
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        temporary.write_text(worker_id + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    return {"id": worker_id, "name": socket.gethostname()}


def _worker_id() -> str:
    return f"wrk_{secrets.token_hex(12)}"


def _manifest_has_indices(manifest: dict[str, object]) -> bool:
    cameras = manifest.get("cameras") or []
    motion = manifest.get("motion") or {}
    actions = manifest.get("robot_actions") or {}
    return any("index" in item for item in cameras) or "index" in motion or any(
        "index" in item for item in actions.get("sources", [])
    )


def _nearest_entry(entries: list[list[int]], time_ns: int) -> list[int]:
    target = int(time_ns)
    low = 0
    high = len(entries)
    while low < high:
        middle = (low + high) // 2
        if entries[middle][0] < target:
            low = middle + 1
        else:
            high = middle
    if low <= 0:
        return entries[0]
    if low >= len(entries):
        return entries[-1]
    before = entries[low - 1]
    after = entries[low]
    return before if target - before[0] <= after[0] - target else after


def _read_frame_slice(manifest_path: Path, relative_path: str, entry: list[int]) -> bytes:
    path = (manifest_path.parent / relative_path).resolve()
    if not _is_relative_to(path, manifest_path.parent.resolve()):
        raise ValueError("播放帧路径超出缓存目录")
    with path.open("rb") as source:
        source.seek(entry[1])
        payload = source.read(entry[2])
    if len(payload) != entry[2]:
        raise ValueError("播放帧缓存读取不完整")
    return payload


def _frame_metadata(entry: list[int], time_ns: int, total: int) -> dict[str, int | bool]:
    return {
        "frame_offset_ns": entry[0],
        "skew_ns": entry[0] - int(time_ns),
        "frame_index": entry[3],
        "end_of_stream": entry[3] == total - 1,
    }


def _time_ns(query: dict[str, list[str]]) -> int:
    try:
        value = int(query.get("time_ns", [""])[0])
    except ValueError as exc:
        raise ValueError("播放时间无效") from exc
    if value < 0:
        raise ValueError("播放时间必须为非负数")
    return value


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
