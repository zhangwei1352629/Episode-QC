from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import mimetypes
import os
from pathlib import Path
import queue
import re
import secrets
import threading
import traceback
import shutil
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit
import webbrowser

from episode_qc.platform_workflow import (
    FlowClient,
    FlowClientError,
    QualityCacheError,
    QualityCacheManager,
)
from episode_qc.playback import (
    ACTION_FRAME_ENCODING,
    MOTION_FRAME_ENCODING,
    prepare_episode_cache,
    public_cache_manifest,
)
from episode_qc.source_paths import resolve_source_directory
from episode_qc.workspace import (
    backup_workspace_database,
    delete_annotation,
    episode_detail,
    export_workspace,
    import_label_schema,
    install_flow_label_schema,
    activate_label_set,
    clear_local_task_history,
    delete_label_set,
    initialize_workspace,
    list_qc_tasks,
    list_label_sets,
    mark_qc_task_submitted,
    preview_label_schema,
    redo_annotation_change,
    rescan_qc_task,
    save_annotation,
    scan_data_source,
    sync_flow_previous_reviews,
    undo_annotation_change,
    update_episode_review,
    update_workspace_settings,
    WorkspaceConflictError,
    workspace_state,
)


ENTITY_ID = re.compile(r"^(?:ep|str|ann)_[a-f0-9]{24,32}$")
ACTION_KEYS = {"policy", "policy_target", "policy_command", "soma"}
WEB_TOKEN_FILE = ".web-token"
LOCAL_WEB_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WILDCARD_WEB_HOSTS = frozenset({"0.0.0.0", "::"})
PLATFORM_CACHE_CLEANUP_INTERVAL_SECONDS = 60 * 60
PLATFORM_CLAIM_HEARTBEAT_INTERVAL_SECONDS = 30
PLATFORM_RESULT_RECONCILE_INTERVAL_SECONDS = 5 * 60
NAS_PROBE_PATH_ENV = "EPISODE_QC_NAS_PROBE_PATH"
NAS_UNAVAILABLE_MESSAGE = (
    "NAS 当前不可用；可继续查看本机已有任务，依赖 NAS 的领取、缓存、导入和提交操作将在恢复后可用。"
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebPaths:
    root: Path
    db_path: Path
    cache_root: Path
    static_root: Path
    default_profile: Path
    default_label_schema: Path
    ego_label_schema: Path


class NasStatusMonitor:
    """Probe a potentially slow network path without blocking HTTP requests."""

    def __init__(self, probe_path: str) -> None:
        self._probe_path = probe_path.strip()
        self._lock = threading.Lock()
        self._probe_in_progress = False
        self._closed = False
        if self._probe_path:
            self._status: dict[str, object] = {
                "configured": True,
                "available": False,
                "path": self._probe_path,
                "message": NAS_UNAVAILABLE_MESSAGE,
            }
            self.refresh()
        else:
            self._status = {"configured": False, "available": True}

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def status(self) -> dict[str, object]:
        self.refresh()
        with self._lock:
            return dict(self._status)

    def refresh(self) -> None:
        with self._lock:
            if self._closed or not self._probe_path or self._probe_in_progress:
                return
            self._probe_in_progress = True
        threading.Thread(
            target=self._probe_once,
            name="episode-qc-nas-probe",
            daemon=True,
        ).start()

    def _probe_once(self) -> None:
        try:
            available = Path(self._probe_path).is_dir()
        except OSError:
            available = False
        status: dict[str, object] = {
            "configured": True,
            "available": available,
            "path": self._probe_path,
        }
        if not available:
            status["message"] = NAS_UNAVAILABLE_MESSAGE
        with self._lock:
            if not self._closed:
                self._status = status
            self._probe_in_progress = False


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
        ego_label_schema=(
            packaged_static / "label-schema-ego-manual.yaml"
            if packaged_static.is_dir()
            else project_root / "app" / "renderer" / "label-schema-ego-manual.yaml"
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

    def remove(self, episode_id: str) -> None:
        with self._lock:
            self._items.pop(episode_id, None)

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


class PlatformCacheCleanup:
    """Schedule only the existing safe retention policy for one QC workspace."""

    def __init__(self, manager_factory, *, interval_seconds: int) -> None:
        self._manager_factory = manager_factory
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.run_once("startup")
        self._thread = threading.Thread(
            target=self._run,
            name="episode-qc-platform-cache-cleanup",
            daemon=True,
        )
        self._thread.start()

    def run_once(self, source: str) -> None:
        try:
            summary = self._manager_factory().evict_expired()
        except Exception:
            LOGGER.exception("platform cache cleanup failed source=%s", source)
            return
        LOGGER.info(
            "platform cache cleanup source=%s scanned=%s evicted=%s freed_bytes=%s failed=%s",
            source,
            summary.get("scanned_jobs", 0),
            len(summary.get("evicted_jobs", [])),
            summary.get("freed_bytes", 0),
            len(summary.get("failed_jobs", {})),
        )

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self.run_once("periodic")


class EpisodeQcWebApplication:
    def __init__(
        self,
        paths: WebPaths,
        *,
        token: str | None = None,
        flow_enabled: bool = True,
        require_token: bool = True,
    ) -> None:
        self.paths = paths
        self.token = token or secrets.token_urlsafe(32)
        self.events = EventHub()
        self.playback = PlaybackRegistry(paths.cache_root)
        self.pending_label_schema: Path | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="episode-qc-cache")
        self._platform_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="episode-qc-platform"
        )
        self._platform_progress_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="episode-qc-progress"
        )
        self._jobs: set[str] = set()
        self._jobs_lock = threading.Lock()
        self._platform_jobs: set[str] = set()
        self._platform_owned_jobs: set[str] = set()
        self._platform_history_synced_jobs: set[str] = set()
        self._platform_ownership_errors: dict[str, str] = {}
        self._platform_progress: dict[str, dict[str, object]] = {}
        self._platform_result_jobs: set[str] = set()
        self._platform_lock = threading.RLock()
        self._flow_client_factory = FlowClient
        self._flow_client: FlowClient | None = None
        self._flow_connection: dict[str, str] = {}
        self._flow_error = ""
        self.flow_enabled = bool(flow_enabled)
        self.require_token = bool(require_token)
        self.session_id = f"web-{self.token[:12]}"
        paths.root.mkdir(parents=True, exist_ok=True)
        initialize_workspace(paths.db_path)
        self._nas_status_monitor = NasStatusMonitor(
            os.environ.get(NAS_PROBE_PATH_ENV, "")
        )
        self._platform_cache_cleanup = PlatformCacheCleanup(
            self._quality_cache_manager,
            interval_seconds=PLATFORM_CACHE_CLEANUP_INTERVAL_SECONDS,
        )
        self._platform_cache_cleanup.start()
        self._platform_result_reconcile_stop = threading.Event()
        self._connect_platform_from_environment()
        self._platform_heartbeat_stop = threading.Event()
        self._platform_heartbeat_thread = threading.Thread(
            target=self._run_platform_claim_heartbeats,
            name="episode-qc-platform-claim-heartbeat",
            daemon=True,
        )
        self._platform_heartbeat_thread.start()
        self._platform_result_reconcile_thread = threading.Thread(
            target=self._run_platform_result_reconciliation,
            name="episode-qc-platform-result-reconcile",
            daemon=True,
        )
        self._platform_result_reconcile_thread.start()

    def close(self) -> None:
        self._nas_status_monitor.close()
        self._platform_cache_cleanup.close()
        self._platform_heartbeat_stop.set()
        self._platform_heartbeat_thread.join(timeout=5)
        self._platform_result_reconcile_stop.set()
        self._platform_result_reconcile_thread.join(timeout=5)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._platform_executor.shutdown(wait=False, cancel_futures=True)
        self._platform_progress_executor.shutdown(wait=False, cancel_futures=True)

    def nas_status(self) -> dict[str, object]:
        return self._nas_status_monitor.status()

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

    def clear_local_task_history(self, keep_task_id: str | None) -> dict[str, object]:
        result = clear_local_task_history(
            self.paths.db_path, keep_task_id=keep_task_id
        )
        for episode_id in result["removed_episode_ids"]:
            self.playback.remove(str(episode_id))
            cache_path = self.paths.cache_root / "episodes" / str(episode_id)
            if cache_path.is_dir():
                shutil.rmtree(cache_path)
        return result

    def get_label_sets(self) -> dict[str, object]:
        return {"label_sets": list_label_sets(self.paths.db_path)}

    def activate_label_set(self, label_set_id: str) -> dict[str, object]:
        return {
            "active": activate_label_set(self.paths.db_path, label_set_id),
            "label_sets": list_label_sets(self.paths.db_path),
        }

    def delete_label_set(self, label_set_id: str) -> dict[str, object]:
        return delete_label_set(self.paths.db_path, label_set_id)

    def get_platform_reviewers(self, request: dict[str, object]) -> dict[str, object]:
        self._assert_flow_enabled()
        base_url = str(request.get("baseUrl") or "").strip().rstrip("/")
        if not base_url:
            raise ValueError("请填写 Flow 地址")
        client = self._flow_client_factory(base_url, None, None)
        response = client.reviewers()
        return {"base_url": base_url, **response}

    def connect_platform(self, request: dict[str, object]) -> dict[str, object]:
        self._assert_flow_enabled()
        base_url = str(request.get("baseUrl") or "").strip().rstrip("/")
        employee_no = str(request.get("employeeNo") or "").strip()
        username = str(request.get("username") or "").strip()
        password = str(request.get("password") or "")
        if not base_url:
            raise ValueError("请填写 Flow 地址")
        if employee_no:
            client = self._flow_client_factory(base_url, None, None)
            login = client.login_reviewer(employee_no)
            reviewer = login.get("reviewer") or {}
            username = employee_no
            reviewer_name = str(
                reviewer.get("display_name") if isinstance(reviewer, dict) else reviewer
            )
        else:
            if not username or not password:
                raise ValueError("请刷新并选择质检员")
            client = self._flow_client_factory(base_url, username, password)
            reviewer_name = username
        response = client.jobs_response()
        with self._platform_lock:
            self._flow_client = client
            self._flow_connection = {
                "base_url": base_url,
                "username": username,
                "employee_no": employee_no,
                "reviewer": str(response.get("reviewer") or reviewer_name),
            }
            self._flow_error = ""
            self._platform_history_synced_jobs.clear()
        self._refresh_platform_owned_jobs(response)
        self._sync_existing_platform_review_histories(client, response)
        self._resume_incomplete_platform_caches(client, response)
        self._schedule_platform_result_reconciliation(
            client,
            response,
            source="connect",
        )
        return self._platform_payload(response)

    def disconnect_platform(self) -> dict[str, object]:
        self._assert_flow_enabled()
        with self._platform_lock:
            if self._platform_jobs or self._platform_result_jobs:
                raise ValueError("仍有质检任务正在缓存或同步结果，暂时不能退出 Flow")
            self._flow_client = None
            self._flow_connection = {}
            self._flow_error = ""
            self._platform_owned_jobs.clear()
            self._platform_history_synced_jobs.clear()
            self._platform_ownership_errors.clear()
        return {"connected": False, "jobs": []}

    def get_platform_jobs(self) -> dict[str, object]:
        if not self.flow_enabled:
            return {"enabled": False, "connected": False, "jobs": []}
        client = self._require_flow_client(allow_missing=True)
        if client is None:
            return {
                "enabled": True,
                "connected": False,
                "jobs": [],
                "error": self._flow_error,
                "default_base_url": os.environ.get(
                    "EPISODE_QC_FLOW_URL", "http://127.0.0.1:8000"
                ),
                "default_username": os.environ.get("EPISODE_QC_FLOW_USERNAME", ""),
            }
        response = client.jobs_response()
        self._refresh_platform_owned_jobs(response)
        self._sync_existing_platform_review_histories(client, response)
        return self._platform_payload(response)

    def _refresh_platform_owned_jobs(self, response: dict[str, object]) -> None:
        reviewer = str(response.get("reviewer") or "")
        if not reviewer:
            return
        owned = {
            str(item.get("code"))
            for item in response.get("jobs", [])
            if isinstance(item, dict)
            and item.get("code")
            and item.get("reviewer_name") == reviewer
            and item.get("status") not in {"completed", "waiting_data"}
        }
        with self._platform_lock:
            self._platform_owned_jobs = owned
            self._platform_ownership_errors = {
                code: message
                for code, message in self._platform_ownership_errors.items()
                if code in owned
            }

    def _sync_existing_platform_review_histories(
        self,
        client,
        response: dict[str, object],
    ) -> None:
        """Upgrade already-cached owned jobs with complete incremental history."""

        reviewer = str(response.get("reviewer") or "")
        if not reviewer:
            return
        for item in response.get("jobs", []):
            if (
                not isinstance(item, dict)
                or not item.get("code")
                or item.get("reviewer_name") != reviewer
                or item.get("status") in {"completed", "waiting_data"}
            ):
                continue
            job_code = str(item["code"])
            if self._local_task_for_job(job_code) is None:
                continue
            with self._platform_lock:
                if job_code in self._platform_history_synced_jobs:
                    continue
            try:
                self._platform_job(client, job_code)
            except (FlowClientError, OSError, QualityCacheError, ValueError) as exc:
                LOGGER.warning(
                    "Flow QC history sync failed for existing job %s: %s",
                    job_code,
                    exc,
                )
                continue
            with self._platform_lock:
                self._platform_history_synced_jobs.add(job_code)

    def _heartbeat_platform_claims_once(self) -> None:
        with self._platform_lock:
            client = self._flow_client
            job_codes = sorted(self._platform_owned_jobs)
        if client is None:
            return
        for job_code in job_codes:
            try:
                client.heartbeat(job_code)
            except FlowClientError as exc:
                with self._platform_lock:
                    self._platform_ownership_errors[job_code] = str(exc)
                    if exc.status_code in {403, 409}:
                        self._platform_owned_jobs.discard(job_code)
                continue
            with self._platform_lock:
                self._platform_ownership_errors.pop(job_code, None)

    def _run_platform_claim_heartbeats(self) -> None:
        while not self._platform_heartbeat_stop.wait(
            PLATFORM_CLAIM_HEARTBEAT_INTERVAL_SECONDS
        ):
            self._heartbeat_platform_claims_once()

    def _result_reconcile_interval_seconds(self) -> float:
        try:
            configured = float(
                os.environ.get(
                    "EPISODE_QC_RESULT_RECONCILE_INTERVAL_SECONDS",
                    str(PLATFORM_RESULT_RECONCILE_INTERVAL_SECONDS),
                )
            )
        except ValueError:
            configured = PLATFORM_RESULT_RECONCILE_INTERVAL_SECONDS
        return max(30.0, configured)

    def _run_platform_result_reconciliation(self) -> None:
        while not self._platform_result_reconcile_stop.wait(
            self._result_reconcile_interval_seconds()
        ):
            self._reconcile_platform_results_once("periodic")

    def _reconcile_platform_results_once(self, source: str) -> None:
        with self._platform_lock:
            client = self._flow_client
        if client is None:
            return
        try:
            response = client.jobs_response()
        except Exception as exc:
            LOGGER.warning("Flow QC result patrol failed source=%s: %s", source, exc)
            return
        self._refresh_platform_owned_jobs(response)
        self._schedule_platform_result_reconciliation(
            client,
            response,
            source=source,
        )

    def _schedule_platform_result_reconciliation(
        self,
        client,
        response: dict[str, object],
        *,
        source: str,
    ) -> None:
        """Schedule idempotent submission for complete local unsynced work."""

        with self._platform_lock:
            if client is not self._flow_client:
                return
        visible_jobs = {
            str(item.get("code")): item
            for item in response.get("jobs", [])
            if isinstance(item, dict) and item.get("code")
        }
        manager = self._quality_cache_manager()
        for task in list_qc_tasks(self.paths.db_path):
            job_code = str(task.get("flow_job_code") or "")
            if not job_code or task.get("status") != "completed":
                continue
            job = visible_jobs.get(job_code)
            if job is None or job.get("status") == "waiting_data":
                continue
            try:
                summary = manager.cache_summary(job_code)
            except QualityCacheError as exc:
                LOGGER.warning("QC result patrol skipped %s: %s", job_code, exc)
                continue
            if (
                not summary
                or not summary.get("cache_complete")
                or summary.get("result_synced") is True
            ):
                continue
            # A completed Flow job with a pending local result is the narrow
            # crash window after Flow accepted the idempotent submission but
            # before the QC state file was marked synced. Replaying it is safe.
            if job.get("status") == "completed" and not summary.get("pending_result"):
                continue
            with self._platform_lock:
                if job_code in self._platform_result_jobs:
                    continue
                self._platform_result_jobs.add(job_code)
            self._platform_executor.submit(
                self._reconcile_platform_result,
                job_code,
                source,
            )

    def _reconcile_platform_result(self, job_code: str, source: str) -> None:
        try:
            result = self._submit_platform_job_once(job_code)
        except Exception as exc:
            try:
                self._quality_cache_manager().record_result_sync_error(
                    job_code,
                    str(exc),
                )
            except Exception:
                LOGGER.exception("failed to record QC result patrol error job=%s", job_code)
            LOGGER.warning(
                "QC result patrol failed source=%s job=%s: %s",
                source,
                job_code,
                exc,
            )
        else:
            LOGGER.info(
                "QC result patrol synced source=%s job=%s flow_status=%s",
                source,
                job_code,
                (result.get("job") or {}).get("status"),
            )
        finally:
            with self._platform_lock:
                self._platform_result_jobs.discard(job_code)

    def _sync_platform_review_progress(self, job_code: str) -> dict[str, object] | None:
        with self._platform_lock:
            client = self._flow_client
        if client is None or not hasattr(client, "report_review_progress"):
            return None
        task = next(
            (
                item
                for item in list_qc_tasks(self.paths.db_path)
                if item.get("flow_job_code") == job_code
            ),
            None,
        )
        if not task or not task.get("review_started_at"):
            return None
        manager = self._quality_cache_manager()
        mappings = manager.local_episode_mappings(job_code)
        completed_episodes = []
        for mapping in mappings:
            episode = episode_detail(
                self.paths.db_path, mapping["local_episode_id"]
            )["episode"]
            if episode.get("review_status") not in {"completed", "reviewed"}:
                continue
            completed_episodes.append(
                {
                    "episode_id": mapping["episode_id"],
                    "completed_at": episode.get("reviewed_at") or episode["updated_at"],
                }
            )
        metadata = task.get("metadata")
        job = metadata.get("flow_job") if isinstance(metadata, dict) else None
        expected_ids = {
            item.get("episode_id")
            for item in (job or {}).get("episodes", [])
            if item.get("episode_id")
        }
        completed_ids = {item["episode_id"] for item in completed_episodes}
        review_completed_at = (
            task.get("review_completed_at")
            if expected_ids and completed_ids == expected_ids
            else None
        )
        return client.report_review_progress(
            job_code,
            review_started_at=str(task["review_started_at"]),
            review_completed_at=(
                str(review_completed_at) if review_completed_at else None
            ),
            completed_episodes=completed_episodes,
        )

    def _schedule_platform_review_progress(self, episode_id: str) -> None:
        try:
            task_id = episode_detail(self.paths.db_path, episode_id)["episode"].get(
                "task_id"
            )
            task = next(
                (
                    item
                    for item in list_qc_tasks(self.paths.db_path)
                    if item["id"] == task_id
                ),
                None,
            )
        except (KeyError, OSError):
            return
        job_code = str((task or {}).get("flow_job_code") or "")
        if not job_code:
            return

        def sync() -> None:
            try:
                self._sync_platform_review_progress(job_code)
            except Exception as exc:
                LOGGER.warning("Flow QC progress sync failed for %s: %s", job_code, exc)

        self._platform_progress_executor.submit(sync)

    def _resume_incomplete_platform_caches(self, client, response: dict[str, object]) -> None:
        """Continue durable Episode queues once a reviewer reconnects to Flow."""
        manager = self._quality_cache_manager()
        for item in response.get("jobs", []):
            if not isinstance(item, dict):
                continue
            job_code = str(item.get("code") or "")
            if not job_code:
                continue
            try:
                manager.flush_pending_cache_report(client, job_code)
                summary = manager.cache_summary(job_code)
            except QualityCacheError:
                continue
            if item.get("status") in {"completed", "waiting_data"}:
                continue
            if summary and summary.get("cache_status") == "failed":
                # A schema/pre-cache failure has no local Episode queue to
                # resume. Its durable Flow report is retried above only.
                continue
            if summary is None or summary.get("cache_complete"):
                continue
            with self._platform_lock:
                if job_code in self._platform_jobs:
                    continue
                self._platform_jobs.add(job_code)
            self._platform_executor.submit(self._cache_platform_job, client, job_code)

    def claim_platform_job(self, job_code: str) -> dict[str, object]:
        self._assert_flow_enabled()
        client = self._require_flow_client()
        job = self._platform_job(client, job_code)
        if job.get("status") == "waiting_data":
            raise ValueError("质检批次的数据尚未完成 NAS 传输和校验")
        if job.get("status") == "completed":
            raise ValueError("质检任务已经完成")
        local_task = self._local_task_for_job(job_code)
        manager = self._quality_cache_manager()
        summary = None
        if local_task and local_task.get("status") != "failed":
            summary = manager.cache_summary(job_code)
            if (
                summary
                and summary.get("cache_complete")
                and job.get("status") != "pending"
            ):
                return {"accepted": False, "job": job, "local_task": local_task}
        pre_cache_failure = manager.has_pre_cache_failure(job_code)
        if summary and not summary.get("cache_complete"):
            # The job is already owned locally.  Resuming its durable queue
            # must not re-claim an active Flow work session.
            with self._platform_lock:
                if job_code in self._platform_jobs:
                    return {"accepted": False, "job": job, "caching": True}
                self._platform_jobs.add(job_code)
            self._platform_executor.submit(self._cache_platform_job, client, job_code)
            return {"accepted": True, "job": job, "caching": True}
        cache_recovery = bool(local_task and summary is None)
        workspace_backup = None
        if cache_recovery:
            workspace_backup = backup_workspace_database(
                self.paths.db_path,
                self.paths.root / "backups",
                reason=f"cache-recovery-{job_code}",
            )
        claimed = client.claim(job_code)
        with self._platform_lock:
            self._platform_owned_jobs.add(job_code)
            self._platform_ownership_errors.pop(job_code, None)
        if pre_cache_failure:
            # Clear only after Flow has accepted the explicit retry. A failed
            # claim must leave the durable failure/error available to retry.
            manager.clear_pre_cache_failure(job_code)
        with self._platform_lock:
            if job_code in self._platform_jobs:
                return {"accepted": False, "job": claimed, "caching": True}
            self._platform_jobs.add(job_code)
        self._platform_executor.submit(self._cache_platform_job, client, job_code)
        response = {"accepted": True, "job": claimed, "caching": True}
        if cache_recovery and workspace_backup is not None:
            response.update(
                cache_recovery=True,
                workspace_backup=workspace_backup.name,
            )
        return response

    def start_platform_job(self, job_code: str) -> dict[str, object]:
        self._assert_flow_enabled()
        client = self._require_flow_client()
        job = self._platform_job(client, job_code)
        task = self._local_task_for_job(job_code)
        if task is None:
            raise ValueError("质检任务尚未完整缓存到本地")
        if task.get("status") in {"submitted", "archived"}:
            return {"job": job, "local_task": task, "started": False}
        if job.get("status") == "in_progress":
            return {"job": job, "local_task": task, "started": False}
        if job.get("status") == "pending":
            raise ValueError("质检任务领取已失效，请先重新领取")
        manager = self._quality_cache_manager()
        manager.start_review(client, job_code)
        return {
            "job": self._platform_job(client, job_code),
            "local_task": task,
            "started": True,
        }

    def submit_platform_job(self, job_code: str) -> dict[str, object]:
        with self._platform_lock:
            if job_code in self._platform_result_jobs:
                raise ValueError("质检结果正在自动同步，请稍后刷新")
            self._platform_result_jobs.add(job_code)
        try:
            return self._submit_platform_job_once(job_code)
        finally:
            with self._platform_lock:
                self._platform_result_jobs.discard(job_code)

    def _submit_platform_job_once(self, job_code: str) -> dict[str, object]:
        self._assert_flow_enabled()
        client = self._require_flow_client()
        task = self._local_task_for_job(job_code)
        if task is None:
            raise ValueError("质检任务尚未领取并缓存到本地")
        try:
            job = self._platform_job(client, job_code)
            status = job.get("status")
            ensure_work_session_before_submit = status != "completed"
            reclaim_before_submit = ensure_work_session_before_submit and (
                status != "in_progress" or bool(job.get("lease_expired"))
            )
        except ValueError:
            metadata = task.get("metadata")
            cached_job = metadata.get("flow_job") if isinstance(metadata, dict) else None
            if not isinstance(cached_job, dict) or cached_job.get("code") != job_code:
                raise
            job = dict(cached_job)
            ensure_work_session_before_submit = True
            reclaim_before_submit = True
        manager = self._quality_cache_manager()
        mappings = self._workspace_episode_mappings(job, task)
        if mappings:
            mapping_writer = getattr(manager, "record_local_episodes", None)
            if mapping_writer:
                mapping_writer(job_code, mappings)
        elif str(task.get("id") or "").strip() and isinstance(
            job.get("episodes"), list
        ) and job.get("episodes"):
            raise ValueError(
                "质检结果无法按实际 Episode 目录建立完整映射，已阻止按列表顺序提交"
            )
        else:
            mappings = manager.local_episode_mappings(job_code)
        if not mappings:
            raise ValueError("质检任务缺少本地 Episode 映射")
        episode_results = []
        for mapping in mappings:
            detail = episode_detail(self.paths.db_path, mapping["local_episode_id"])
            episode = detail["episode"]
            decision = episode.get("quality_decision")
            if not decision or episode.get("review_status") not in {"completed", "reviewed"}:
                raise ValueError(f"Episode {mapping['episode_id']} 尚未完成质检")
            episode_results.append(
                {
                    "episode_id": mapping["episode_id"],
                    "decision": decision,
                    "annotation_count": int(episode.get("annotation_count") or 0),
                    "annotations": detail["annotations"],
                    "completed_at": episode.get("reviewed_at"),
                    **(
                        {
                            "relative_episode_path": mapping["relative_path"],
                            "episode_name": Path(str(mapping["relative_path"])).name,
                        }
                        if mapping.get("relative_path")
                        else {}
                    ),
                    "result": {
                        "local_episode_id": mapping["local_episode_id"],
                        **(
                            {
                                "relative_episode_path": mapping["relative_path"],
                                "episode_name": Path(
                                    str(mapping["relative_path"])
                                ).name,
                            }
                            if mapping.get("relative_path")
                            else {}
                        ),
                        "review_status": episode.get("review_status"),
                        "reviewer_name": episode.get("reviewer_name"),
                        "deleted_annotation_lineages": detail.get(
                            "deleted_annotation_lineages", []
                        ),
                    },
                }
            )
        self._sync_platform_review_progress(job_code)
        task = self._local_task_for_job(job_code) or task
        if ensure_work_session_before_submit:
            if reclaim_before_submit:
                client.claim(job_code)
            manager.start_review(client, job_code)
            job = self._platform_job(client, job_code)
        submit_values = {
            "episode_results": episode_results,
            "result": {"episode_count": len(episode_results)},
        }
        if task.get("review_started_at") and task.get("review_completed_at"):
            submit_values.update(
                review_started_at=task["review_started_at"],
                review_completed_at=task["review_completed_at"],
            )
        response = manager.submit_result(client, job, **submit_values)
        local_task = mark_qc_task_submitted(self.paths.db_path, job_code)
        with self._platform_lock:
            self._platform_owned_jobs.discard(job_code)
            self._platform_ownership_errors.pop(job_code, None)
        return {"job": response, "local_task": local_task}

    def _connect_platform_from_environment(self) -> None:
        if not self.flow_enabled:
            return
        base_url = os.environ.get("EPISODE_QC_FLOW_URL", "").strip()
        username = os.environ.get("EPISODE_QC_FLOW_USERNAME", "").strip()
        password = os.environ.get("EPISODE_QC_FLOW_PASSWORD", "")
        if not (base_url and username and password):
            return
        try:
            self.connect_platform(
                {"baseUrl": base_url, "username": username, "password": password}
            )
        except (FlowClientError, ValueError) as exc:
            self._flow_error = str(exc)

    def _require_flow_client(self, *, allow_missing: bool = False):
        self._assert_flow_enabled()
        with self._platform_lock:
            client = self._flow_client
        if client is None and not allow_missing:
            raise ValueError("请先登录 Flow 质检账号")
        return client

    def _assert_flow_enabled(self) -> None:
        if not self.flow_enabled:
            raise ValueError("当前为单机模式，Flow 功能未启用")

    def _platform_payload(self, response: dict[str, object]) -> dict[str, object]:
        tasks = list_qc_tasks(self.paths.db_path)
        local_by_job = {
            str(task["flow_job_code"]): task
            for task in tasks
            if task.get("flow_job_code")
        }
        cache_by_job = {}
        missing_cache_state_jobs = set()
        cache_errors_by_job = {}
        manager = self._quality_cache_manager()
        for job_code in local_by_job:
            try:
                summary = manager.cache_summary(job_code)
            except QualityCacheError as exc:
                cache_errors_by_job[job_code] = str(exc)
            else:
                if summary is None:
                    missing_cache_state_jobs.add(job_code)
                else:
                    cache_by_job[job_code] = summary
        with self._platform_lock:
            caching = set(self._platform_jobs)
            progress_by_job = {
                code: dict(values)
                for code, values in self._platform_progress.items()
            }
            if response.get("reviewer"):
                self._flow_connection["reviewer"] = str(response["reviewer"])
            connection = dict(self._flow_connection)
            ownership_errors = dict(self._platform_ownership_errors)
        jobs = []
        for item in response.get("jobs", []):
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            local_task = local_by_job.get(code)
            cache_summary = cache_by_job.get(code)
            if cache_summary is None and code and code not in local_by_job:
                try:
                    cache_summary = manager.cache_summary(code)
                except QualityCacheError:
                    cache_summary = None
            cache_state_missing = bool(
                local_task and code in missing_cache_state_jobs
            )
            cache_recovery_available = bool(
                cache_state_missing
                and local_task.get("status") not in {"submitted", "archived"}
                and item.get("status") not in {"completed", "waiting_data"}
            )
            cache_summary = cache_summary or {}
            jobs.append(
                {
                    **item,
                    "local_task_id": local_task.get("id") if local_task else None,
                    "local_task_status": local_task.get("status") if local_task else None,
                    "local_caching": code in caching,
                    "cache_state_missing": cache_state_missing,
                    "cache_recovery_available": cache_recovery_available,
                    **(
                        {"cache_state_error": cache_errors_by_job[code]}
                        if code in cache_errors_by_job
                        else {}
                    ),
                    **cache_summary,
                    **(
                        {"local_progress": progress_by_job[code]}
                        if code in caching and code in progress_by_job
                        else {}
                    ),
                    **(
                        {"ownership_error": ownership_errors[code]}
                        if code in ownership_errors
                        else {}
                    ),
                }
            )
        return {"enabled": True, "connected": True, **connection, "jobs": jobs}

    def _local_task_for_job(self, job_code: str) -> dict[str, object] | None:
        return next(
            (
                task
                for task in list_qc_tasks(self.paths.db_path)
                if task.get("flow_job_code") == job_code
            ),
            None,
        )

    def _workspace_episode_mappings(
        self,
        job: dict[str, object],
        local_task: dict[str, object],
        *,
        require_complete: bool = True,
    ) -> list[dict[str, object]]:
        """Rebuild safe Flow mappings for legacy tasks without cache state."""

        task_id = str(local_task.get("id") or "").strip()
        platform_episodes = job.get("episodes")
        if not task_id or not isinstance(platform_episodes, list):
            return []
        try:
            local_episodes = workspace_state(
                self.paths.db_path,
                task_id=task_id,
            ).get("episodes", [])
        except (KeyError, OSError, ValueError):
            return []
        if not isinstance(local_episodes, list):
            return []

        def normalized_relative_path(value: object) -> str:
            parts = [
                part
                for part in str(value or "").replace("\\", "/").split("/")
                if part not in {"", "."}
            ]
            if not parts or ".." in parts:
                return ""
            return "/".join(parts)

        local_by_path: dict[str, str] = {}
        for item in local_episodes:
            if not isinstance(item, dict):
                return []
            relative_path = normalized_relative_path(item.get("relative_path"))
            local_episode_id = str(item.get("id") or "").strip()
            if (
                not relative_path
                or not local_episode_id
                or relative_path in local_by_path
            ):
                return []
            local_by_path[relative_path] = local_episode_id

        mappings: list[dict[str, object]] = []
        seen_platform_paths: set[str] = set()
        seen_local_episode_ids: set[str] = set()
        for item in platform_episodes:
            if not isinstance(item, dict):
                return []
            relative_path = normalized_relative_path(item.get("relative_path"))
            platform_episode_id = str(item.get("episode_id") or "").strip()
            if (
                not relative_path
                or not platform_episode_id
                or relative_path in seen_platform_paths
            ):
                return []
            seen_platform_paths.add(relative_path)
            candidate_paths = [relative_path]
            primary_file = normalized_relative_path(item.get("primary_file"))
            if primary_file:
                candidate_paths.append(
                    normalized_relative_path(f"{relative_path}/{primary_file}")
                )
            matched_local_ids = {
                local_by_path[path]
                for path in candidate_paths
                if path in local_by_path
            }
            if len(matched_local_ids) > 1:
                return []
            local_episode_id = next(iter(matched_local_ids), "")
            if local_episode_id:
                if local_episode_id in seen_local_episode_ids:
                    return []
                seen_local_episode_ids.add(local_episode_id)
                mappings.append(
                    {
                        "episode_id": platform_episode_id,
                        "local_episode_id": local_episode_id,
                        "relative_path": relative_path,
                    }
                )
        if len(mappings) != len(local_by_path):
            return []
        if require_complete and len(mappings) != len(platform_episodes):
            return []
        return mappings

    def _platform_job(self, client, job_code: str) -> dict[str, object]:
        if hasattr(client, "job"):
            job = client.job(job_code)
        else:
            job = next(
                (item for item in client.jobs() if item.get("code") == job_code),
                None,
            )
        if job is None:
            raise ValueError(f"Flow 中不存在或当前账号无权访问质检任务：{job_code}")
        local_task = self._local_task_for_job(job_code)
        episodes = job.get("episodes")
        has_previous_review = isinstance(episodes, list) and any(
            isinstance(item, dict)
            and ("previous_review" in item or "review_history" in item)
            for item in episodes
        )
        if local_task is not None and has_previous_review:
            manager = self._quality_cache_manager()
            mapping_reader = getattr(manager, "local_episode_mappings", None)
            try:
                mappings = mapping_reader(job_code) if mapping_reader else []
            except (OSError, QualityCacheError):
                mappings = []
            workspace_mappings = self._workspace_episode_mappings(
                job,
                local_task,
                require_complete=False,
            )
            if workspace_mappings:
                mappings = workspace_mappings
                mapping_writer = getattr(manager, "record_local_episodes", None)
                if mapping_writer:
                    mapping_writer(job_code, mappings)
            elif (
                str(local_task.get("id") or "").strip()
                and episodes
                and all(
                    isinstance(item, dict) and item.get("relative_path")
                    for item in episodes
                )
            ):
                mappings = []
            if mappings:
                sync_flow_previous_reviews(self.paths.db_path, job, mappings)
        return job

    def _quality_cache_manager(self) -> QualityCacheManager:
        try:
            reserve_gb = max(
                0.0, float(os.environ.get("EPISODE_QC_CACHE_RESERVE_GB", "10"))
            )
        except ValueError:
            reserve_gb = 10.0
        return QualityCacheManager(
            self.paths.root / "platform-cache",
            reserve_bytes=int(reserve_gb * 1024**3),
        )

    def _cache_platform_job(self, client, job_code: str) -> None:
        manager = self._quality_cache_manager()
        local_ready = False
        indexed_task_id = ""
        review_started = False
        job: dict[str, object] = {}
        bound_label_set_id: str | None = None

        def publish_progress(values: dict[str, object]) -> None:
            with self._platform_lock:
                self._platform_progress[job_code] = dict(values)
            self.events.publish(
                {"type": "platform_job", "jobCode": job_code, **values}
            )

        def index_ready_episode(values: dict[str, object]) -> None:
            nonlocal indexed_task_id, local_ready, review_started
            profile_path = (
                self.paths.default_profile
                if self.paths.default_profile.is_file()
                else None
            )
            viewer_profile = str(job.get("viewer_profile") or "")
            asset_type = str(job.get("asset_type") or "")
            task_kind = (
                "ego_omniego"
                if viewer_profile == "ego_omniego" or asset_type == "egocentric"
                else "robot_teleoperation"
            )
            indexed = scan_data_source(
                self.paths.db_path,
                str(values["cache_dir"]),
                profile_path=profile_path,
                task_code=job_code,
                task_name=str(job.get("task_name") or job.get("asset_id") or job_code),
                origin="flow",
                flow_job_code=job_code,
                asset_id=str(job.get("asset_id") or "") or None,
                label_set_id=bound_label_set_id,
                source_uri=str(job.get("source_uri") or job.get("asset_nas_uri") or ""),
                task_metadata={"flow_job": job},
                task_kind=task_kind,
                annotation_mode=str(
                    job.get("annotation_mode")
                    or ("open" if task_kind == "ego_omniego" else "library")
                ),
                annotation_schema_version=str(
                    job.get("annotation_schema_version")
                    or ("ego_open_v1" if task_kind == "ego_omniego" else "")
                ),
            )
            ready_by_path = {
                str(Path(item["relative_path"]).as_posix()).strip("./"): item
                for item in indexed["episodes"]
                if item["import_status"] == "ready"
            }
            mappings = []
            for platform_episode in job.get("episodes", []):
                relative_path = str(
                    Path(platform_episode["relative_path"]).as_posix()
                ).strip("./")
                primary_file = str(platform_episode.get("primary_file") or "").strip("./")
                candidate_paths = [relative_path]
                if primary_file:
                    candidate_paths.append(
                        str((Path(relative_path) / primary_file).as_posix()).strip("./")
                    )
                local_episode = next(
                    (ready_by_path.get(path) for path in candidate_paths if ready_by_path.get(path)),
                    None,
                )
                if local_episode is not None:
                    mappings.append(
                        {
                            "episode_id": platform_episode["episode_id"],
                            "local_episode_id": local_episode["id"],
                            "relative_path": relative_path,
                        }
                    )
            if len(mappings) != len(ready_by_path):
                raise QualityCacheError(
                    f"资产缓存包含未登记的 Episode：Flow {len(mappings)} 个，"
                    f"本地 {len(ready_by_path)} 个"
                )
            if not mappings:
                raise QualityCacheError("本地缓存尚未索引到已验证的 Flow Episode")
            manager.record_local_episodes(job_code, mappings)
            if any(
                "previous_review" in item or "review_history" in item
                for item in job.get("episodes", [])
            ):
                sync_flow_previous_reviews(self.paths.db_path, job, mappings)
            local_ready = True
            indexed_task_id = str(indexed["task_id"])
            cache_summary = manager.cache_summary(job_code) or {}
            cache_progress = (
                int(
                    int(cache_summary.get("cached_bytes") or 0)
                    * 100
                    / int(cache_summary.get("total_bytes") or 1)
                )
                if cache_summary.get("total_bytes")
                else 0
            )
            warning = ""
            if not review_started:
                review_started = True
                try:
                    started = manager.start_review(client, job_code)
                except Exception as exc:
                    # Local review may still be opened. The explicit start action
                    # can retry a transient Flow work-session conflict later.
                    warning = str(exc)
                else:
                    publish_progress(
                        {
                            "status": "caching",
                            "progress": cache_progress,
                            "taskId": indexed_task_id,
                            "job": started,
                            "cached_episode_count": values.get("cached_episode_count", 0),
                            "total_episode_count": values.get("total_episode_count", 0),
                        }
                    )
                    return
            publish_progress(
                {
                    "status": "caching",
                    "progress": cache_progress,
                    "taskId": indexed_task_id,
                    "warning": warning,
                    "cached_episode_count": values.get("cached_episode_count", 0),
                    "total_episode_count": values.get("total_episode_count", 0),
                }
            )

        try:
            job = self._platform_job(client, job_code)
            if job.get("label_set_id") or job.get("status") not in {
                "claimed",
                "caching",
                "cache_ready",
                "in_progress",
            }:
                job = client.claim(job_code)
            installed_label_set = (
                {"active": False}
                if str(job.get("annotation_mode") or "") == "open"
                else install_flow_label_schema(self.paths.db_path, job)
            )
            bound_label_set_id = (
                str(installed_label_set["id"])
                if installed_label_set.get("id")
                else None
            )
            cached = manager.cache_job(
                client,
                job,
                progress_callback=publish_progress,
                episode_ready_callback=index_ready_episode,
            )
            if not local_ready:
                raise QualityCacheError("完整缓存未建立本地 Episode 任务")
            publish_progress(
                {
                    "status": "ready",
                    "progress": 100,
                    "taskId": indexed_task_id,
                    "cached_episode_count": cached.get("total_episode_count", 0),
                    "total_episode_count": cached.get("total_episode_count", 0),
                }
            )
        except Exception as exc:
            if job:
                try:
                    manager.record_cache_failure(job_code, str(exc))
                except (OSError, QualityCacheError):
                    # If the failure journal itself cannot be persisted, keep
                    # the previous best-effort direct report as a last resort.
                    try:
                        client.report_cache(
                            job_code,
                            status="failed",
                            cache_error=str(exc),
                        )
                    except Exception:
                        pass
                else:
                    try:
                        manager.flush_pending_cache_report(client, job_code)
                    except QualityCacheError:
                        # The durable report remains available for reconnect.
                        pass
            else:
                try:
                    client.report_cache(
                        job_code,
                        status="failed",
                        cache_error=str(exc),
                    )
                except Exception:
                    pass
            publish_progress(
                {
                    "status": "cache_failed" if local_ready else "failed",
                    "taskId": indexed_task_id or None,
                    "error": str(exc),
                }
            )
        finally:
            with self._platform_lock:
                self._platform_jobs.discard(job_code)
                self._platform_progress.pop(job_code, None)

    def add_source(self, request: dict[str, object]) -> dict[str, object]:
        root_path = request.get("rootPath")
        if not isinstance(root_path, str) or not root_path.strip():
            raise ValueError("请输入数据源目录")
        profile_path = self.paths.default_profile if self.paths.default_profile.is_file() else None
        task_kind = str(request.get("taskKind") or "robot_teleoperation")
        return scan_data_source(
            self.paths.db_path,
            root_path,
            profile_path=profile_path,
            task_kind=task_kind,
            annotation_mode="open" if task_kind == "ego_omniego" else "library",
            annotation_schema_version="ego_open_v1" if task_kind == "ego_omniego" else "",
        )

    def rescan_task(self, task_id: str) -> dict[str, object]:
        profile_path = self.paths.default_profile if self.paths.default_profile.is_file() else None
        return rescan_qc_task(self.paths.db_path, task_id, profile_path=profile_path)

    def prepare_episode(self, episode_id: str) -> dict[str, object]:
        detail = episode_detail(self.paths.db_path, episode_id)
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
        updated = update_episode_review(
            self.paths.db_path,
            episode_id,
            review_status=request.get("status") if isinstance(request.get("status"), str) else None,
            quality_decision=request.get("decision") if isinstance(request.get("decision"), str) else None,
            reviewer_name=request.get("reviewer") if isinstance(request.get("reviewer"), str) else None,
            last_playhead_ns=round(float(playhead)) if isinstance(playhead, (int, float)) else None,
        )
        if isinstance(request.get("status"), str) or isinstance(request.get("decision"), str):
            self._schedule_platform_review_progress(episode_id)
        return updated

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
    ) -> None:
        self.application = application
        self.allowed_hosts = allowed_hosts
        self.public_hosts = public_hosts
        super().__init__(server_address, EpisodeQcRequestHandler)

    @property
    def allowed_origins(self) -> frozenset[str]:
        port = self.server_address[1]
        return frozenset(_http_origin(host, port) for host in self.allowed_hosts)


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
        except (
            FileNotFoundError,
            FlowClientError,
            QualityCacheError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _assert_api_access(self, parsed: Any) -> None:
        origin = self.headers.get("Origin")
        if self.application.require_token:
            query_token = parse_qs(parsed.query).get("token", [""])[0]
            supplied = self.headers.get("X-Episode-QC-Token", "") or query_token
            if not hmac.compare_digest(supplied, self.application.token):
                raise PermissionError("访问令牌无效")
        if self.command != "GET" and origin and origin not in self.server.allowed_origins:  # type: ignore[attr-defined]
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
        if not self.application.require_token:
            return False
        supplied = parse_qs(parsed.query).get("token", [""])[0]
        if hmac.compare_digest(supplied, self.application.token):
            return False
        if not self._client_is_loopback():
            raise PermissionError("局域网访问必须使用启动终端打印的完整令牌地址")
        location = f"/?token={quote(self.application.token, safe='')}"
        self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        return True

    def _client_is_loopback(self) -> bool:
        address = str(self.client_address[0]).split("%", 1)[0]
        try:
            return ipaddress.ip_address(address).is_loopback
        except ValueError:
            return False

    def _route_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        app = self.application
        if method == "GET" and path == "/api/health":
            self._send_json({"ok": True, "nas": app.nas_status()})
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
        if method == "DELETE" and path == "/api/tasks/history":
            keep_task_id = query.get("keep_task_id", [None])[0]
            self._send_json(app.clear_local_task_history(keep_task_id))
            return
        if method == "POST" and path == "/api/platform/reviewers":
            self._send_json(app.get_platform_reviewers(self._json_body()))
            return
        if method == "POST" and path == "/api/platform/login":
            self._send_json(app.connect_platform(self._json_body()))
            return
        if method == "POST" and path == "/api/platform/logout":
            self._discard_body()
            self._send_json(app.disconnect_platform())
            return
        if method == "GET" and path == "/api/platform/jobs":
            self._send_json(app.get_platform_jobs())
            return
        platform_claim_match = re.fullmatch(
            r"/api/platform/jobs/([A-Za-z0-9._-]+)/claim", path
        )
        if method == "POST" and platform_claim_match:
            self._discard_body()
            self._send_json(
                app.claim_platform_job(platform_claim_match.group(1)),
                HTTPStatus.ACCEPTED,
            )
            return
        platform_submit_match = re.fullmatch(
            r"/api/platform/jobs/([A-Za-z0-9._-]+)/submit", path
        )
        platform_start_match = re.fullmatch(
            r"/api/platform/jobs/([A-Za-z0-9._-]+)/start", path
        )
        if method == "POST" and platform_start_match:
            self._discard_body()
            self._send_json(app.start_platform_job(platform_start_match.group(1)))
            return
        if method == "POST" and platform_submit_match:
            self._discard_body()
            self._send_json(app.submit_platform_job(platform_submit_match.group(1)))
            return
        if method == "POST" and path in {"/api/sources", "/api/tasks/import"}:
            self._send_json(app.add_source(self._json_body()))
            return
        task_rescan_match = re.fullmatch(r"/api/tasks/(tsk_[a-f0-9]{24,32})/rescan", path)
        if method == "POST" and task_rescan_match:
            self._discard_body()
            self._send_json(app.rescan_task(task_rescan_match.group(1)))
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
        if method == "GET" and path == "/api/label-sets":
            self._send_json(app.get_label_sets())
            return
        label_set_match = re.fullmatch(r"/api/label-sets/(ls_[a-f0-9]{24,32})", path)
        if method == "DELETE" and label_set_match:
            self._discard_body()
            self._send_json(app.delete_label_set(label_set_match.group(1)))
            return
        label_set_activate_match = re.fullmatch(
            r"/api/label-sets/(ls_[a-f0-9]{24,32})/activate", path
        )
        if method == "POST" and label_set_activate_match:
            self._discard_body()
            self._send_json(app.activate_label_set(label_set_activate_match.group(1)))
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
            r"/api/episodes/(ep_[a-f0-9]{24,32})/actions/(policy|policy_target|policy_command|soma)/frame",
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
        self._send_security_headers()
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
        self._send_security_headers()
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
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()

    def _send_security_headers(self) -> None:
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )

def create_web_server(
    paths: WebPaths,
    *,
    port: int = 0,
    token: str | None = None,
    host: str = "127.0.0.1",
    public_hosts: tuple[str, ...] = (),
    flow_enabled: bool = True,
    require_token: bool = True,
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
    application = EpisodeQcWebApplication(
        paths,
        token=token,
        flow_enabled=flow_enabled,
        require_token=require_token,
    )
    return EpisodeQcWebServer(
        (bind_host, port),
        application,
        allowed_hosts=allowed_hosts,
        public_hosts=advertised_hosts,
    )


def serve_web_app(
    *,
    port: int = 0,
    workspace_root: str | Path | None = None,
    open_browser: bool = True,
    host: str = "127.0.0.1",
    public_hosts: tuple[str, ...] = (),
    flow_enabled: bool = True,
    require_token: bool = True,
) -> None:
    paths = default_web_paths(workspace_root)
    server = create_web_server(
        paths,
        port=port,
        token=persistent_web_token(paths.root),
        host=host,
        public_hosts=public_hosts,
        flow_enabled=flow_enabled,
        require_token=require_token,
    )
    actual_port = server.server_address[1]
    entry_urls = [f"{_http_origin(item, actual_port)}/" for item in server.public_hosts]
    browser_url = (
        f"{entry_urls[0]}?token={quote(server.application.token, safe='')}"
        if require_token
        else entry_urls[0]
    )
    print("Episode QC Web:", flush=True)
    for entry_url in entry_urls:
        entry_host = _normalize_web_host(urlsplit(entry_url).hostname or "")
        if require_token and entry_host not in LOCAL_WEB_HOSTS:
            print(f"  {entry_url}?token={quote(server.application.token, safe='')}", flush=True)
        else:
            print(f"  {entry_url}", flush=True)
    if any(item not in LOCAL_WEB_HOSTS for item in server.public_hosts):
        if require_token:
            print("警告：局域网访问已开启；请只把完整令牌地址交给授权质检员。", flush=True)
        else:
            print("严重警告：免令牌模式已开启；任何可访问此端口的人都拥有完整质检权限。", flush=True)
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
