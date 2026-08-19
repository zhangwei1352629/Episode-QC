"""Flow integration and full local staging for large QC source datasets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable
from urllib.parse import urlsplit

from episode_qc.source_paths import resolve_source_directory, resolve_target_directory


ProgressCallback = Callable[[dict[str, object]], None]
DECISION_MAP = {
    "pass": "pass",
    "pass_with_labels": "pass_with_labels",
    "trim": "trim_required",
    "trim_required": "trim_required",
    "repair": "repair_required",
    "repair_required": "repair_required",
    "recollect": "recollect",
    "reject": "discard",
    "discard": "discard",
}


class FlowClientError(RuntimeError):
    pass


class QualityCacheError(RuntimeError):
    pass


_FLOW_LABEL_REFERENCE_FIELDS = (
    "label_set_id",
    "label_schema_version",
    "label_schema_hash",
)
_FLOW_ANNOTATION_FIELDS = (
    "label_code",
    "scope",
    "start_offset_ns",
    "end_offset_ns",
    "target_type",
    "target_key",
    "severity",
    "action",
    "comment",
    "attributes",
)


def _flow_label_set_reference(job: dict) -> dict | None:
    """Return the complete frozen Flow label reference, if this is a labeled job."""
    values = {field: job.get(field) for field in _FLOW_LABEL_REFERENCE_FIELDS}
    provided = [value is not None and value != "" for value in values.values()]
    if any(provided) and not all(provided):
        raise QualityCacheError("Flow 标签库引用必须同时包含标签集、版本和哈希")
    if not any(provided):
        return None
    return values


def _flow_annotations(value: object) -> list[dict]:
    """Keep only Flow fact fields and rename QC-local annotation IDs."""
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise QualityCacheError("annotations 必须是对象列表")
    normalized_annotations = []
    for annotation in value:
        normalized = {
            field: annotation[field]
            for field in _FLOW_ANNOTATION_FIELDS
            if field in annotation
        }
        if "annotation_id" in annotation:
            normalized["id"] = annotation["annotation_id"]
        elif "id" in annotation:
            normalized["id"] = annotation["id"]
        normalized_annotations.append(normalized)
    return normalized_annotations


def _api_error_message(value: object) -> str:
    """Flatten DRF error payloads without assuming the payload is an object."""

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "；".join(filter(None, (_api_error_message(item) for item in value)))
    if isinstance(value, dict):
        if value.get("detail"):
            return _api_error_message(value["detail"])
        messages = []
        for field, detail in value.items():
            rendered = _api_error_message(detail)
            if rendered:
                messages.append(rendered if field in {"error", "non_field_errors"} else f"{field}: {rendered}")
        return "；".join(messages)
    return str(value)


class FlowClient:
    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        *,
        token: str | None = None,
        timeout: int = 30,
    ):
        self.base_url = str(base_url).strip().rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise FlowClientError("Flow 地址必须以 http:// 或 https:// 开头")
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        elif username is not None and password is not None:
            basic_token = base64.b64encode(
                f"{username}:{password}".encode("utf-8")
            ).decode("ascii")
            self.headers["Authorization"] = f"Basic {basic_token}"
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, payload: dict | None = None):
        data = None if method == "GET" else json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=self.headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            message = error.read().decode("utf-8", errors="replace")
            try:
                message = _api_error_message(json.loads(message))
            except json.JSONDecodeError:
                pass
            raise FlowClientError(f"Flow 请求失败（HTTP {error.code}）：{message}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise FlowClientError(f"无法连接 Flow：{error}") from error

    def jobs_response(self, statuses: list[str] | None = None) -> dict:
        suffix = ""
        if statuses:
            suffix = "?status=" + ",".join(statuses)
        return self.request("GET", f"/api/v1/qc/jobs{suffix}")

    def reviewers(self) -> dict:
        return self.request("GET", "/api/v1/reviewers")

    def login_reviewer(self, employee_no: str) -> dict:
        result = self.request(
            "POST", "/api/v1/qc-reviewer-login", {"employee_no": employee_no}
        )
        self.headers["Authorization"] = (
            f"{result.get('token_type', 'Bearer')} {result['token']}"
        )
        return result

    def jobs(self, statuses: list[str] | None = None) -> list[dict]:
        return self.jobs_response(statuses)["jobs"]

    def claim(self, job_code: str) -> dict:
        return self.request("POST", f"/api/v1/qc/jobs/{job_code}/claim", {})

    def release(self, job_code: str) -> dict:
        return self.request("POST", f"/api/v1/qc/jobs/{job_code}/release", {})

    def report_cache(self, job_code: str, **values) -> dict:
        return self.request("POST", f"/api/v1/qc/jobs/{job_code}/cache", values)

    def report_work(self, job_code: str, *, action: str, **values) -> dict:
        return self.request(
            "POST",
            f"/api/v1/qc/jobs/{job_code}/work",
            {"action": action, **values},
        )

    def submit_result(
        self,
        job_code: str,
        *,
        episode_results: list[dict],
        result: dict | None = None,
        label_set: dict | None = None,
        result_nas_path: str = "",
        result_id: str = "",
        result_sha256: str = "",
        result_manifest: dict | None = None,
    ) -> dict:
        normalized_results = []
        for episode_result in episode_results:
            decision = episode_result.get("decision")
            try:
                platform_decision = DECISION_MAP[decision]
            except KeyError as exc:
                raise FlowClientError(f"不支持的质检结论：{decision}") from exc
            normalized = {
                "episode_id": episode_result["episode_id"],
                "decision": platform_decision,
                "annotation_count": int(episode_result.get("annotation_count") or 0),
                "result": episode_result.get("result") or {},
            }
            if episode_result.get("quality_grade"):
                normalized["quality_grade"] = episode_result["quality_grade"]
            if "annotations" in episode_result:
                normalized["annotations"] = _flow_annotations(
                    episode_result["annotations"]
                )
            normalized_results.append(normalized)
        payload = {
            "episode_results": normalized_results,
            "result": result or {},
            "result_nas_path": result_nas_path,
            "result_id": result_id,
            "result_sha256": result_sha256,
            "result_manifest": result_manifest or {},
        }
        if label_set is not None:
            payload["label_set"] = label_set
        return self.request("POST", f"/api/v1/qc/jobs/{job_code}/result", payload)


def sha256_file(path: str | Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class QualityCacheManager:
    def __init__(
        self,
        cache_root: str | Path,
        *,
        reserve_bytes: int = 10 * 1024**3,
        workspace_name: str | None = None,
        chunk_size: int = 8 * 1024 * 1024,
    ):
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.reserve_bytes = int(reserve_bytes)
        self.workspace_name = workspace_name or socket.gethostname()
        self.chunk_size = chunk_size

    def cache_job(
        self,
        client: FlowClient,
        job: dict,
        *,
        progress_callback: ProgressCallback | None = None,
        episode_ready_callback: ProgressCallback | None = None,
    ) -> dict:
        job_code = self._safe_component(job["code"], "质检任务编号")
        claimed = (
            dict(job)
            if job.get("status")
            in {"claimed", "caching", "cache_ready", "in_progress"}
            and isinstance(job.get("label_schema"), dict)
            else client.claim(job_code)
        )
        source = resolve_source_directory(str(claimed["source_uri"]))
        files, asset_manifest_sha256 = self._manifest_file_specs(claimed, source)
        episode_specs, manifest_file = self._episode_file_specs(claimed, files)
        total_bytes = sum(int(item["size_bytes"]) for item in files)
        asset_directory = self._asset_directory_name(claimed)
        partial_job_root = self.cache_root / "downloading" / f"{job_code}.partial"
        partial_root = partial_job_root / asset_directory
        ready_job_root = self.cache_root / "ready" / job_code
        ready_root = ready_job_root / asset_directory
        reused = self._reuse_ready_cache(
            client,
            claimed,
            ready_job_root,
            ready_root,
            total_bytes=total_bytes,
            files=files,
            asset_manifest_sha256=asset_manifest_sha256,
        )
        if reused is not None:
            for episode in episode_specs:
                self._emit_episode_ready(
                    episode_ready_callback,
                    claimed,
                    ready_root,
                    episode,
                    cached_episode_count=len(episode_specs),
                    total_episode_count=len(episode_specs),
                    cache_complete=True,
                    reused=True,
                )
            return reused

        partial_root.mkdir(parents=True, exist_ok=True)
        ready_root.mkdir(parents=True, exist_ok=True)
        state_path = ready_job_root / ".qc-cache.json"
        state = self._load_or_create_progressive_state(
            state_path,
            claimed,
            asset_directory=asset_directory,
            asset_manifest_sha256=asset_manifest_sha256,
            total_bytes=total_bytes,
            episode_specs=episode_specs,
        )
        if state.get("asset_manifest_ready"):
            try:
                self._verify_manifest_files(ready_root, [manifest_file])
            except QualityCacheError:
                state["asset_manifest_ready"] = False
        state_entries = {item["episode_id"]: item for item in state["episodes"]}
        copied_bytes = self._progressive_cached_bytes(
            ready_root,
            partial_root,
            state_entries,
            episode_specs,
            manifest_file,
        )
        if not state.get("cache_complete"):
            state["cache_status"] = (
                "partially_ready" if self._ready_episode_count(state) else "caching"
            )
        self._write_progressive_state(state_path, state, copied_bytes)
        self._report_cache_with_retry(
            client,
            job_code,
            status="caching",
            cache_progress=min(99, int(copied_bytes * 100 / total_bytes)) if total_bytes else 0,
            cached_bytes=copied_bytes,
            cache_workstation=self.workspace_name,
            state_path=state_path,
            state=state,
        )
        last_reported_percent = -1

        def copy_progress(values: dict[str, object]) -> None:
            self._emit(
                progress_callback,
                {
                    **values,
                    "cached_episode_count": self._ready_episode_count(state),
                    "total_episode_count": len(episode_specs),
                },
        )

        if not state.get("asset_manifest_ready"):
            copied_bytes = self._copy_resumable(
                source / manifest_file["relative_path"],
                ready_root / manifest_file["relative_path"],
                copied_bytes=copied_bytes,
                total_bytes=total_bytes,
                callback=copy_progress,
                expected_size=int(manifest_file["size_bytes"]),
            )
            self._verify_manifest_files(
                ready_root,
                [manifest_file],
                progress_callback=lambda values: self._emit(
                    progress_callback,
                    {**values, "verified_files": 1, "total_files": len(files)},
                ),
            )
            state["asset_manifest_ready"] = True
            self._write_progressive_state(state_path, state, copied_bytes)

        primary_files: list[dict] = []
        failed_episodes: list[str] = []
        verified_file_count = 1
        for episode in episode_specs:
            state_entry = state_entries[episode["episode_id"]]
            episode_files = episode["files"]
            was_ready = state_entry.get("status") == "ready"
            ready_episode_root = ready_root / episode["relative_path"]
            verified: dict[str, str] | None = None
            if not was_ready and ready_episode_root.exists():
                # A power loss can occur after the directory move but before
                # the state-file write. Verify the immutable files and adopt
                # that completed Episode instead of treating it as a conflict.
                try:
                    verified = self._verify_manifest_files(ready_root, episode_files)
                except QualityCacheError:
                    shutil.rmtree(ready_episode_root)
                else:
                    was_ready = True
                    copied_bytes += sum(
                        int(item["size_bytes"]) for item in episode_files
                    ) - self._existing_bytes(partial_root, episode_files)
                    state_entry["status"] = "ready"
                    state_entry["cached_bytes"] = sum(
                        int(item["size_bytes"]) for item in episode_files
                    )
                    state_entry["primary_files"] = self._verify_episode_primary_files(
                        {"episodes": [episode["job_episode"]]}, verified
                    )
                    state["cache_status"] = "partially_ready"
                    self._write_progressive_state(state_path, state, copied_bytes)
            if was_ready:
                verified = verified or self._verify_manifest_files(ready_root, episode_files)
            else:
                episode_ready = False
                for retry in range(3):
                    try:
                        self.evict_expired()
                        self._ensure_disk_space(
                            sum(int(item["size_bytes"]) for item in episode_files)
                        )
                        state_entry["status"] = "caching"
                        state_entry["error"] = ""
                        self._write_progressive_state(state_path, state, copied_bytes)
                        copied_before_episode = copied_bytes - self._existing_bytes(
                            partial_root, episode_files
                        )

                        def copy_episode_progress(values: dict[str, object]) -> None:
                            current_copied = int(values["cached_bytes"])
                            state_entry["cached_bytes"] = max(
                                0, current_copied - copied_before_episode
                            )
                            self._write_progressive_state(
                                state_path, state, current_copied
                            )
                            copy_progress(values)

                        for file_spec in episode_files:
                            relative = Path(file_spec["relative_path"])
                            target = partial_root / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            copied_bytes = self._copy_resumable(
                                source / relative,
                                target,
                                copied_bytes=copied_bytes,
                                total_bytes=total_bytes,
                                callback=copy_episode_progress,
                                expected_size=int(file_spec["size_bytes"]),
                            )
                            percent = (
                                min(99, int(copied_bytes * 100 / total_bytes))
                                if total_bytes
                                else 99
                            )
                            if percent >= last_reported_percent + 5:
                                self._report_cache_with_retry(
                                    client,
                                    job_code,
                                    status="caching",
                                    cache_progress=percent,
                                    cached_bytes=copied_bytes,
                                    cache_workstation=self.workspace_name,
                                    state_path=state_path,
                                    state=state,
                                )
                                last_reported_percent = percent
                        verified = self._verify_manifest_files(
                            partial_root,
                            episode_files,
                            progress_callback=lambda values, offset=verified_file_count: self._emit(
                                progress_callback,
                                {
                                    **values,
                                    "verified_files": offset + int(values["verified_files"]),
                                    "total_files": len(files),
                                    "cached_episode_count": self._ready_episode_count(state),
                                    "total_episode_count": len(episode_specs),
                                },
                            ),
                        )
                        primary_files_for_episode = self._verify_episode_primary_files(
                            {"episodes": [episode["job_episode"]]}, verified
                        )
                        source_episode_root = partial_root / episode["relative_path"]
                        ready_episode_root.parent.mkdir(parents=True, exist_ok=True)
                        if ready_episode_root.exists():
                            raise QualityCacheError(
                                f"本地 Episode 缓存目录冲突：{ready_episode_root}"
                            )
                        os.replace(source_episode_root, ready_episode_root)
                        state_entry["status"] = "ready"
                        state_entry["cached_bytes"] = sum(
                            int(item["size_bytes"]) for item in episode_files
                        )
                        state_entry["primary_files"] = primary_files_for_episode
                        state["cache_status"] = "partially_ready"
                        self._write_progressive_state(state_path, state, copied_bytes)
                    except (OSError, QualityCacheError) as exc:
                        state_entry["status"] = "failed"
                        state_entry["error"] = str(exc)
                        state_entry["retry_count"] = retry + 1
                        copied_bytes = self._progressive_cached_bytes(
                            ready_root,
                            partial_root,
                            state_entries,
                            episode_specs,
                            manifest_file,
                        )
                        self._write_progressive_state(state_path, state, copied_bytes)
                        self._emit(
                            progress_callback,
                            {
                                "status": "episode_failed",
                                "episode_id": episode["episode_id"],
                                "error": str(exc),
                                "retry_count": retry + 1,
                                "cached_episode_count": self._ready_episode_count(state),
                                "total_episode_count": len(episode_specs),
                            },
                        )
                        if retry == 2:
                            failed_episodes.append(
                                f"{episode['episode_id']}: {exc}"
                            )
                            break
                        time.sleep(0.1 * (retry + 1))
                    else:
                        episode_ready = True
                        break
                if not episode_ready:
                    continue

            if not state_entry.get("primary_files"):
                state_entry["primary_files"] = self._verify_episode_primary_files(
                    {"episodes": [episode["job_episode"]]}, verified
                )
                self._write_progressive_state(state_path, state, copied_bytes)
            primary_files.extend(state_entry["primary_files"])
            self._emit_episode_ready(
                episode_ready_callback,
                claimed,
                ready_root,
                episode,
                cached_episode_count=self._ready_episode_count(state),
                total_episode_count=len(episode_specs),
                cache_complete=False,
                reused=was_ready,
            )
            self._preserve_local_episode_mappings(state_path, state)
            verified_file_count += len(episode_files)

        if failed_episodes:
            state["cache_complete"] = False
            state["cache_status"] = "failed"
            state["cache_error"] = "；".join(failed_episodes)
            self._write_progressive_state(state_path, state, copied_bytes)
            raise QualityCacheError(f"Episode 缓存失败：{state['cache_error']}")

        state["cache_complete"] = True
        state["cache_status"] = "cache_ready"
        state["primary_files"] = primary_files
        self._write_progressive_state(state_path, state, total_bytes)
        response = self._report_cache_with_retry(
            client,
            job_code,
            status="cache_ready",
            cache_progress=100,
            cached_bytes=total_bytes,
            cache_workstation=self.workspace_name,
            state_path=state_path,
            state=state,
        )
        self._emit(
            progress_callback,
            {
                "status": "cache_ready",
                "progress": 100,
                "cached_bytes": total_bytes,
                "cached_episode_count": len(episode_specs),
                "total_episode_count": len(episode_specs),
            },
        )
        return {
            "job": response or claimed,
            "cache_dir": str(ready_root),
            "primary_files": [str(ready_root / item["path"]) for item in primary_files],
            "total_bytes": total_bytes,
            "cache_complete": True,
            "cached_episode_count": len(episode_specs),
            "total_episode_count": len(episode_specs),
            "reused": False,
        }

    def _report_cache_with_retry(
        self,
        client: FlowClient,
        job_code: str,
        *,
        state_path: Path | None = None,
        state: dict | None = None,
        **values,
    ) -> dict | None:
        """Best-effort Flow reporting must not interrupt local cache progress."""
        last_error = ""
        for attempt in range(3):
            try:
                response = client.report_cache(job_code, **values)
            except (FlowClientError, OSError) as exc:
                last_error = str(exc)
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
                continue
            if state_path is not None and state is not None:
                state.pop("pending_cache_report", None)
                state.pop("cache_report_error", None)
                self._write_json_atomic(state_path, state)
            return response
        if state_path is not None and state is not None:
            state["pending_cache_report"] = dict(values)
            state["cache_report_error"] = last_error
            self._write_json_atomic(state_path, state)
        return None

    def start_review(self, client: FlowClient, job_code: str) -> dict:
        job_code = self._safe_component(job_code, "质检任务编号")
        if hasattr(client, "report_work"):
            return client.report_work(
                job_code,
                action="start",
                workstation=self.workspace_name,
            )
        return client.report_cache(
            job_code,
            status="in_progress",
            cache_progress=100,
            cache_workstation=self.workspace_name,
        )

    def submit_result(
        self,
        client: FlowClient,
        job: dict,
        *,
        episode_results: list[dict],
        result: dict | None = None,
    ) -> dict:
        job_code = self._safe_component(job["code"], "质检任务编号")
        ready_root = self.cache_root / "ready" / job_code
        state_path = ready_root / ".qc-cache.json"
        if not state_path.is_file():
            raise QualityCacheError("质检任务尚未完整缓存到本地")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("asset_id") != job.get("asset_id"):
            raise QualityCacheError("本地缓存与质检任务的数据资产不一致")
        if not state.get("cache_complete"):
            raise QualityCacheError("质检任务尚未完整缓存到本地，不能提交整批结果")
        expected_ids = {item["episode_id"] for item in job.get("episodes", [])}
        cached_episodes = {
            str(item.get("episode_id") or ""): item
            for item in state.get("episodes") or []
            if isinstance(item, dict)
        }
        if (
            set(cached_episodes) != expected_ids
            or any(item.get("status") != "ready" for item in cached_episodes.values())
        ):
            raise QualityCacheError("质检任务尚未完整缓存到本地，不能提交整批结果")
        submitted_ids = {item.get("episode_id") for item in episode_results}
        if len(submitted_ids) != len(episode_results) or submitted_ids != expected_ids:
            raise QualityCacheError("必须一次提交资产内全部且不重复的 Episode 质检结论")
        label_set = _flow_label_set_reference(job)
        normalized_results = []
        for episode_result in episode_results:
            decision = episode_result.get("decision")
            quality_grade = episode_result.get("quality_grade")
            if decision not in DECISION_MAP:
                raise QualityCacheError(f"不支持的质检结论：{decision}")
            if quality_grade not in {None, "excellent", "good", "medium", "poor", "invalid"}:
                raise QualityCacheError(f"不支持的质量等级：{quality_grade}")
            if int(episode_result.get("annotation_count") or 0) < 0:
                raise QualityCacheError("标注数量不能为负数")
            has_direct_annotations = "annotations" in episode_result
            annotations = (
                _flow_annotations(episode_result["annotations"])
                if has_direct_annotations
                else []
            )
            annotation_count = int(episode_result.get("annotation_count") or 0)
            if label_set is None and annotation_count > 0:
                raise QualityCacheError("无 Flow 标签库引用的质检结果只允许零标注")
            if label_set is not None or has_direct_annotations:
                if annotation_count != len(annotations):
                    raise QualityCacheError("annotation_count 必须等于 annotations 数量")
            if label_set is None and annotations:
                raise QualityCacheError("带有标注的质检结果需要完整的 Flow 标签库引用")
            normalized = {
                **episode_result,
                "decision": DECISION_MAP[episode_result["decision"]],
                "annotation_count": annotation_count,
            }
            if label_set is not None or has_direct_annotations:
                normalized["annotations"] = annotations
            normalized_results.append(normalized)
        pending_result = state.get("pending_result") or {}
        result_id = str(pending_result.get("result_id") or f"QCR-{uuid.uuid4().hex}")
        attempt = int(state.get("next_attempt") or job.get("next_attempt") or 1)
        created_at = str(
            pending_result.get("created_at")
            or datetime.now(timezone.utc).isoformat()
        )
        result_document = {
            "schema_version": 2,
            "result_id": result_id,
            "job_code": job_code,
            "asset_id": job["asset_id"],
            "attempt": attempt,
            "source_manifest_sha256": state.get("asset_manifest_sha256", ""),
            "episode_results": normalized_results,
            "result": result or {},
        }
        local_results = self.cache_root / "results-pending" / job_code
        local_results.mkdir(parents=True, exist_ok=True)
        local_result = local_results / "qc_result.json"
        encoded_result = (
            json.dumps(result_document, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        result_sha256 = hashlib.sha256(encoded_result).hexdigest()
        if pending_result and (
            pending_result.get("result_id") != result_id
            or pending_result.get("result_sha256") != result_sha256
        ):
            raise QualityCacheError("已有待同步质检结果且内容不同，禁止覆盖")
        self._write_json_atomic(local_result, result_document)
        result_manifest = {
            "schema_version": 1,
            "result_id": result_id,
            "result_sha256": result_sha256,
            "job_code": job_code,
            "asset_id": job["asset_id"],
            "attempt": attempt,
            "source_manifest_sha256": state.get("asset_manifest_sha256", ""),
            "created_at": created_at,
            "files": [
                {
                    "relative_path": "qc_result.json",
                    "size_bytes": local_result.stat().st_size,
                    "sha256": result_sha256,
                }
            ],
        }
        state["pending_result"] = {
            "result_id": result_id,
            "result_sha256": result_sha256,
            "attempt": attempt,
            "created_at": created_at,
        }
        self._write_json_atomic(state_path, state)
        result_nas_path = self._publish_result(
            job,
            state,
            local_result,
            result_manifest,
        )
        if hasattr(client, "report_work"):
            client.report_work(job_code, action="heartbeat")
        response = client.submit_result(
            job_code,
            episode_results=normalized_results,
            result=result or {},
            label_set=label_set,
            result_nas_path=result_nas_path,
            result_id=result_id,
            result_sha256=result_sha256,
            result_manifest=result_manifest,
        )
        state["result_synced"] = True
        state["result_synced_at"] = datetime.now(timezone.utc).isoformat()
        state["result_nas_path"] = result_nas_path
        state["result_id"] = result_id
        state["result_sha256"] = result_sha256
        self._write_json_atomic(state_path, state)
        shutil.rmtree(local_results)
        return response

    def record_local_episodes(self, job_code: str, mappings: list[dict]) -> None:
        state_path = self._state_path(job_code)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["local_episodes"] = mappings
        self._write_json_atomic(state_path, state)

    def record_cache_failure(self, job_code: str, cache_error: str) -> None:
        """Durably queue a Flow failure without discarding an existing cache state."""
        safe_code = self._safe_component(job_code, "质检任务编号")
        message = str(cache_error).strip() or "缓存前校验失败"
        ready_state_path = self.cache_root / "ready" / safe_code / ".qc-cache.json"
        if ready_state_path.is_file():
            state_path = ready_state_path
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise QualityCacheError("本地 Episode 缓存状态损坏，请人工检查") from exc
            if state.get("job_code") != safe_code:
                raise QualityCacheError("本地 Episode 缓存任务编号不一致")
        else:
            state_path = self._pre_cache_failure_state_path(safe_code)
            state = {
                "schema_version": 3,
                "job_code": safe_code,
                "cache_complete": False,
                "cached_episode_count": 0,
                "total_episode_count": 0,
                "cached_bytes": 0,
                "total_bytes": 0,
            }
        state["cache_status"] = "failed"
        state["cache_error"] = message
        state["pending_cache_report"] = {
            "status": "failed",
            "cache_error": message,
        }
        state.pop("cache_report_error", None)
        self._write_json_atomic(state_path, state)

    def record_pre_cache_failure(self, job_code: str, cache_error: str) -> None:
        """Compatibility wrapper for callers that fail before cache creation."""
        self.record_cache_failure(job_code, cache_error)

    def has_pre_cache_failure(self, job_code: str) -> bool:
        safe_code = self._safe_component(job_code, "质检任务编号")
        return self._pre_cache_failure_state_path(safe_code).is_file()

    def clear_pre_cache_failure(self, job_code: str) -> None:
        """Clear a failed pre-cache journal only for an explicit user retry."""
        safe_code = self._safe_component(job_code, "质检任务编号")
        failed_root = self._pre_cache_failure_state_path(safe_code).parent
        if failed_root.is_dir():
            shutil.rmtree(failed_root)

    def local_episode_mappings(self, job_code: str) -> list[dict]:
        state_path = self._state_path(job_code)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return list(state.get("local_episodes") or [])

    def cache_summary(self, job_code: str) -> dict[str, object] | None:
        safe_code = self._safe_component(job_code, "质检任务编号")
        state_path = self.cache_root / "ready" / safe_code / ".qc-cache.json"
        if not state_path.is_file():
            state_path = self._pre_cache_failure_state_path(safe_code)
        if not state_path.is_file():
            return None
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QualityCacheError("本地 Episode 缓存状态损坏，请人工检查") from exc
        if state.get("job_code") != safe_code:
            raise QualityCacheError("本地 Episode 缓存任务编号不一致")
        summary = {
            "cache_complete": bool(state.get("cache_complete")),
            "cache_status": str(state.get("cache_status") or "caching"),
            "cached_episode_count": int(state.get("cached_episode_count") or 0),
            "total_episode_count": int(state.get("total_episode_count") or 0),
            "cached_bytes": int(state.get("cached_bytes") or 0),
            "total_bytes": int(state.get("total_bytes") or 0),
        }
        if state.get("cache_error"):
            summary["cache_error"] = str(state["cache_error"])
        return summary

    def flush_pending_cache_report(self, client: FlowClient, job_code: str) -> bool:
        """Retry a durable Flow cache-status report after reconnecting."""
        safe_code = self._safe_component(job_code, "质检任务编号")
        state_path = self.cache_root / "ready" / safe_code / ".qc-cache.json"
        if not state_path.is_file():
            state_path = self._pre_cache_failure_state_path(safe_code)
        if not state_path.is_file():
            return False
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QualityCacheError("本地 Episode 缓存状态损坏，请人工检查") from exc
        pending = state.get("pending_cache_report")
        if not isinstance(pending, dict):
            return False
        return self._report_cache_with_retry(
            client,
            safe_code,
            state_path=state_path,
            state=state,
            **pending,
        ) is not None

    def _state_path(self, job_code: str) -> Path:
        safe_code = self._safe_component(job_code, "质检任务编号")
        state_path = self.cache_root / "ready" / safe_code / ".qc-cache.json"
        if not state_path.is_file():
            raise QualityCacheError("质检任务尚未完整缓存到本地")
        return state_path

    def _pre_cache_failure_state_path(self, job_code: str) -> Path:
        safe_code = self._safe_component(job_code, "质检任务编号")
        return self.cache_root / "failed" / safe_code / ".qc-cache.json"

    def evict(self, job_code: str) -> None:
        job_code = self._safe_component(job_code, "质检任务编号")
        ready_root = self.cache_root / "ready" / job_code
        state_path = ready_root / ".qc-cache.json"
        if not state_path.is_file():
            raise QualityCacheError("本地缓存不存在")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not state.get("result_synced"):
            raise QualityCacheError("质检结果尚未同步，禁止清理本地缓存")
        shutil.rmtree(ready_root)

    def evict_expired(
        self,
        *,
        now: datetime | None = None,
        retention: timedelta = timedelta(days=1),
    ) -> dict[str, object]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        summary = {
            "scanned_jobs": 0,
            "evicted_jobs": [],
            "skipped_jobs": [],
            "failed_jobs": {},
            "freed_bytes": 0,
        }
        ready_root = self.cache_root / "ready"
        if not ready_root.is_dir():
            return summary
        for job_root in sorted(path for path in ready_root.iterdir() if path.is_dir()):
            job_code = job_root.name
            summary["scanned_jobs"] += 1
            try:
                self._safe_component(job_code, "质检任务编号")
                state = json.loads((job_root / ".qc-cache.json").read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise QualityCacheError("缓存状态必须是对象")
            except (OSError, json.JSONDecodeError, QualityCacheError) as exc:
                summary["failed_jobs"][job_code] = str(exc)
                continue
            if state.get("result_synced") is not True:
                summary["skipped_jobs"].append(job_code)
                continue
            try:
                synced_at = datetime.fromisoformat(str(state["result_synced_at"]))
            except (KeyError, TypeError, ValueError):
                summary["skipped_jobs"].append(job_code)
                continue
            if synced_at.tzinfo is None or current < synced_at.astimezone(timezone.utc) + retention:
                summary["skipped_jobs"].append(job_code)
                continue
            try:
                freed_bytes = sum(path.stat().st_size for path in job_root.rglob("*") if path.is_file())
                self.evict(job_code)
            except (OSError, json.JSONDecodeError, QualityCacheError) as exc:
                summary["failed_jobs"][job_code] = str(exc)
                continue
            summary["evicted_jobs"].append(job_code)
            summary["freed_bytes"] += freed_bytes
        return summary

    def _publish_result(
        self,
        job: dict,
        state: dict,
        local_result: Path,
        result_manifest: dict,
    ) -> str:
        job_code = self._safe_component(job["code"], "质检任务编号")
        upload_uri = str(
            job.get("result_upload_uri")
            or state.get("result_upload_uri")
            or ""
        ).rstrip("/")
        if not upload_uri:
            raise QualityCacheError("Flow 没有返回独立的质检结果上传目录")
        self._validate_result_job_root(
            upload_uri,
            asset_id=str(job["asset_id"]),
            job_code=job_code,
            field_name="质检结果上传目录",
        )
        source_uri = str(job.get("source_uri") or job.get("asset_nas_uri") or "")
        if source_uri and self._uri_is_within(upload_uri, source_uri):
            raise QualityCacheError("质检结果目录不能位于不可变原始资产目录内")
        result_root = resolve_target_directory(upload_uri)
        attempt_name = f"attempt-{int(result_manifest['attempt']):04d}"
        destination = result_root / attempt_name
        if destination.exists():
            if self._verify_published_result(destination, result_manifest):
                return f"{upload_uri}/{attempt_name}/qc_result.json"

        staging_uri = str(
            job.get("result_staging_uri")
            or state.get("result_staging_uri")
            or ""
        ).rstrip("/")
        if staging_uri:
            self._validate_result_job_root(
                staging_uri,
                asset_id=str(job["asset_id"]),
                job_code=job_code,
                field_name="质检结果暂存目录",
            )
            if self._normalized_uri(staging_uri) == self._normalized_uri(upload_uri):
                raise QualityCacheError("质检结果暂存目录不能与正式目录相同")
            upload_namespace = self._storage_namespace(upload_uri)
            staging_namespace = self._storage_namespace(staging_uri)
            if (
                upload_namespace is not None
                and staging_namespace is not None
                and upload_namespace != staging_namespace
            ):
                raise QualityCacheError("质检结果暂存目录与正式目录必须位于同一存储卷或 SMB 共享")
            staging_root = resolve_target_directory(staging_uri)
            staging = staging_root / (
                f"{attempt_name}.{result_manifest['result_id']}.partial"
            )
        else:
            staging = result_root / (
                f".{attempt_name}.{result_manifest['result_id']}.partial"
            )
        result_root.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            if result_root.stat().st_dev != staging.parent.stat().st_dev:
                raise QualityCacheError("质检结果暂存目录与正式目录不在同一文件系统，无法原子发布")
        except OSError as exc:
            raise QualityCacheError(f"无法确认质检结果目录所在文件系统：{exc}") from exc
        staged_result = staging / "qc_result.json"
        temporary_result = staging / "qc_result.json.partial"
        shutil.copy2(local_result, temporary_result)
        if sha256_file(temporary_result) != result_manifest["result_sha256"]:
            raise QualityCacheError("质检结果上传后 SHA-256 校验失败")
        os.replace(temporary_result, staged_result)
        self._write_json_atomic(staging / "result_manifest.json", result_manifest)
        try:
            os.replace(staging, destination)
        except OSError as exc:
            try:
                self._verify_published_result(destination, result_manifest)
            except QualityCacheError:
                raise QualityCacheError(f"无法原子发布质检结果：{exc}") from exc
        self._verify_published_result(destination, result_manifest)
        return f"{upload_uri}/{attempt_name}/qc_result.json"

    @staticmethod
    def _normalized_uri(value: str) -> str:
        return str(value).strip().replace("\\", "/").rstrip("/").casefold()

    @classmethod
    def _uri_is_within(cls, candidate: str, parent: str) -> bool:
        normalized_candidate = cls._normalized_uri(candidate)
        normalized_parent = cls._normalized_uri(parent)
        return normalized_candidate == normalized_parent or normalized_candidate.startswith(
            normalized_parent + "/"
        )

    @classmethod
    def _validate_result_job_root(
        cls,
        value: str,
        *,
        asset_id: str,
        job_code: str,
        field_name: str,
    ) -> None:
        expected_suffix = f"/{asset_id}/{job_code}".casefold()
        if not cls._normalized_uri(value).endswith(expected_suffix):
            raise QualityCacheError(
                f"{field_name}必须以当前资产和任务编号结尾：{asset_id}/{job_code}"
            )

    @staticmethod
    def _storage_namespace(value: str) -> tuple[str, ...] | None:
        normalized = str(value).strip().replace("\\", "/")
        lowered = normalized.casefold()
        if lowered.startswith("smb://"):
            parsed = urlsplit(normalized)
            parts = [part for part in parsed.path.split("/") if part]
            if parsed.hostname and parts:
                return ("network", parsed.hostname.casefold(), parts[0].casefold())
        if normalized.startswith("//"):
            parts = [part for part in normalized[2:].split("/") if part]
            if len(parts) >= 2:
                return ("network", parts[0].casefold(), parts[1].casefold())
        drive = re.match(r"^([A-Za-z]):/", normalized)
        if drive:
            return ("drive", drive.group(1).casefold())
        return None

    @staticmethod
    def _verify_published_result(destination: Path, expected_manifest: dict) -> bool:
        manifest_path = destination / "result_manifest.json"
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QualityCacheError(f"已有质检结果目录缺少有效清单：{destination}") from exc
        if existing != expected_manifest:
            raise QualityCacheError(f"质检结果轮次已经存在且清单不同，禁止覆盖：{destination}")
        files = existing.get("files") or []
        if len(files) != 1 or files[0].get("relative_path") != "qc_result.json":
            raise QualityCacheError(f"质检结果清单文件范围无效：{destination}")
        result_path = destination / "qc_result.json"
        expected_size = int(files[0].get("size_bytes") or 0)
        expected_sha256 = str(files[0].get("sha256") or "").lower()
        if not result_path.is_file() or result_path.stat().st_size != expected_size:
            raise QualityCacheError(f"已发布质检结果文件大小校验失败：{result_path}")
        if sha256_file(result_path) != expected_sha256:
            raise QualityCacheError(f"已发布质检结果文件 SHA-256 校验失败：{result_path}")
        return True

    def _ensure_disk_space(self, source_bytes: int) -> None:
        free = shutil.disk_usage(self.cache_root).free
        # Keep room for both the immutable local source copy and the derived
        # playback cache, which can approach the source size for camera data.
        required = source_bytes * 2 + self.reserve_bytes
        if free < required:
            raise QualityCacheError(
                f"本地磁盘空间不足：需要至少 {required} 字节，可用 {free} 字节"
            )

    @staticmethod
    def _asset_directory_name(job: dict) -> str:
        candidate = str(job.get("asset_id") or "asset").strip()
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(candidate).name).strip("._")
        return cleaned or "asset"

    @staticmethod
    def _safe_component(value: object, field_name: str) -> str:
        text = str(value or "")
        if not text or text in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", text):
            raise QualityCacheError(f"{field_name} 不是安全的路径名称")
        return text

    @staticmethod
    def _safe_relative_path(value: object, field_name: str) -> Path:
        normalized = str(value or "").replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or not path.parts or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise QualityCacheError(f"{field_name} 不是安全的相对路径")
        return Path(*path.parts)

    def _episode_file_specs(self, job: dict, files: list[dict]) -> tuple[list[dict], dict]:
        manifest_files = [item for item in files if item["relative_path"] == "asset_manifest.json"]
        if len(manifest_files) != 1:
            raise QualityCacheError("资产缓存缺少 asset_manifest.json")
        manifest_file = manifest_files[0]
        unassigned = {
            item["relative_path"]: item
            for item in files
            if item["relative_path"] != "asset_manifest.json"
        }
        episodes = []
        for job_episode in job.get("episodes") or []:
            episode_id = str(job_episode.get("episode_id") or "")
            relative_directory = self._safe_relative_path(
                job_episode.get("relative_path"), "Episode 相对目录"
            ).as_posix()
            prefix = f"{relative_directory}/"
            episode_files = [
                item
                for path, item in list(unassigned.items())
                if path.startswith(prefix)
            ]
            if not episode_files:
                raise QualityCacheError(f"Episode {episode_id or '?'} 缺少缓存文件")
            for item in episode_files:
                unassigned.pop(item["relative_path"], None)
            episodes.append(
                {
                    "episode_id": episode_id,
                    "relative_path": relative_directory,
                    "job_episode": job_episode,
                    "files": sorted(
                        episode_files, key=lambda item: item["relative_path"]
                    ),
                }
            )
        if unassigned:
            raise QualityCacheError(
                "资产清单文件不属于当前质检任务 Episode："
                + ", ".join(sorted(unassigned))
            )
        return episodes, manifest_file

    def _load_or_create_progressive_state(
        self,
        state_path: Path,
        job: dict,
        *,
        asset_directory: str,
        asset_manifest_sha256: str,
        total_bytes: int,
        episode_specs: list[dict],
    ) -> dict:
        expected_episodes = [
            {
                "episode_id": item["episode_id"],
                "relative_path": item["relative_path"],
            }
            for item in episode_specs
        ]
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise QualityCacheError("已有本地 Episode 缓存状态损坏，请人工检查") from exc
            stored_episodes = [
                {
                    "episode_id": str(item.get("episode_id") or ""),
                    "relative_path": str(item.get("relative_path") or ""),
                }
                for item in state.get("episodes") or []
                if isinstance(item, dict)
            ]
            matches = (
                state.get("schema_version") == 3
                and state.get("job_code") == job.get("code")
                and state.get("asset_id") == job.get("asset_id")
                and state.get("asset_manifest_sha256") == asset_manifest_sha256
                and int(state.get("total_bytes") or 0) == total_bytes
                and stored_episodes == expected_episodes
            )
            if not matches:
                raise QualityCacheError("已有本地 Episode 缓存与当前 Flow 任务不一致，禁止覆盖")
            return state
        return {
            "schema_version": 3,
            "job_code": str(job["code"]),
            "asset_id": str(job["asset_id"]),
            "episode_ids": [item["episode_id"] for item in episode_specs],
            "episodes": [
                {
                    "episode_id": item["episode_id"],
                    "relative_path": item["relative_path"],
                    "files": item["files"],
                    "status": "not_cached",
                    "cached_bytes": 0,
                    "total_bytes": sum(
                        int(file_spec["size_bytes"])
                        for file_spec in item["files"]
                    ),
                    "error": "",
                    "primary_files": [],
                }
                for item in episode_specs
            ],
            "source_uri": job["source_uri"],
            "asset_manifest_sha256": asset_manifest_sha256,
            "asset_manifest_ready": False,
            "cache_complete": False,
            "cache_status": "caching",
            "total_bytes": total_bytes,
            "cached_bytes": 0,
            "primary_files": [],
            "result_synced": False,
            "result_upload_uri": job.get("result_upload_uri", ""),
            "result_staging_uri": job.get("result_staging_uri", ""),
            "next_attempt": int(job.get("next_attempt") or 1),
            "asset_directory": asset_directory,
        }

    def _progressive_cached_bytes(
        self,
        ready_root: Path,
        partial_root: Path,
        state_entries: dict[str, dict],
        episode_specs: list[dict],
        manifest_file: dict,
    ) -> int:
        copied = 0
        manifest_path = ready_root / manifest_file["relative_path"]
        if manifest_path.is_file() and manifest_path.stat().st_size == int(manifest_file["size_bytes"]):
            if sha256_file(manifest_path) == manifest_file["sha256"]:
                copied += int(manifest_file["size_bytes"])
            else:
                manifest_path.unlink()
        for episode in episode_specs:
            entry = state_entries[episode["episode_id"]]
            episode_files = episode["files"]
            if entry.get("status") == "ready":
                try:
                    self._verify_manifest_files(ready_root, episode_files)
                except QualityCacheError:
                    entry["status"] = "not_cached"
                    entry["primary_files"] = []
                else:
                    copied += sum(int(item["size_bytes"]) for item in episode_files)
                    continue
            partial_bytes = self._existing_bytes(partial_root, episode_files)
            entry["cached_bytes"] = partial_bytes
            copied += partial_bytes
        return copied

    @staticmethod
    def _ready_episode_count(state: dict) -> int:
        return sum(
            1
            for item in state.get("episodes") or []
            if isinstance(item, dict) and item.get("status") == "ready"
        )

    def _write_progressive_state(
        self,
        state_path: Path,
        state: dict,
        copied_bytes: int,
    ) -> None:
        state["cached_bytes"] = copied_bytes
        state["cached_episode_count"] = self._ready_episode_count(state)
        state["total_episode_count"] = len(state.get("episodes") or [])
        self._write_json_atomic(state_path, state)

    @staticmethod
    def _preserve_local_episode_mappings(state_path: Path, state: dict) -> None:
        """Keep Web indexing metadata written by an Episode-ready callback.

        The cache worker owns transfer state in memory, while the Web callback
        indexes the newly ready Episode and atomically stores local mappings.
        Reload that independent field before the worker writes its next state.
        """
        try:
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        mappings = persisted.get("local_episodes")
        if isinstance(mappings, list):
            state["local_episodes"] = mappings

    def _emit_episode_ready(
        self,
        callback: ProgressCallback | None,
        job: dict,
        ready_root: Path,
        episode: dict,
        *,
        cached_episode_count: int,
        total_episode_count: int,
        cache_complete: bool,
        reused: bool,
    ) -> None:
        self._emit(
            callback,
            {
                "status": "episode_ready",
                "job_code": job["code"],
                "asset_id": job["asset_id"],
                "episode_id": episode["episode_id"],
                "relative_path": episode["relative_path"],
                "cache_dir": str(ready_root),
                "cached_episode_count": cached_episode_count,
                "total_episode_count": total_episode_count,
                "cache_complete": cache_complete,
                "reused": reused,
            },
        )

    def _manifest_file_specs(
        self,
        job: dict,
        source_root: Path,
    ) -> tuple[list[dict], str]:
        manifest = job.get("asset_manifest") or {}
        if not isinstance(manifest, dict) or not manifest.get("episodes"):
            raise QualityCacheError("Flow 任务缺少完整 asset_manifest，禁止下载")
        if str(manifest.get("asset_id") or "") != str(job.get("asset_id") or ""):
            raise QualityCacheError("Flow 资产清单中的 asset_id 与质检任务不一致")
        platform_episodes = {
            str(item.get("episode_id") or ""): item
            for item in job.get("episodes") or []
        }
        manifest_episodes = {
            str(item.get("episode_id") or ""): item
            for item in manifest.get("episodes") or []
            if isinstance(item, dict)
        }
        if (
            not platform_episodes
            or not set(platform_episodes).issubset(manifest_episodes)
            or "" in manifest_episodes
        ):
            raise QualityCacheError("Flow 资产清单与质检任务的 Episode 范围不一致")
        for episode_id, platform_episode in platform_episodes.items():
            manifest_episode = manifest_episodes[episode_id]
            for field in ("relative_path", "primary_file", "checksum_sha256"):
                left = str(platform_episode.get(field) or "").replace("\\", "/")
                right = str(manifest_episode.get(field) or "").replace("\\", "/")
                if left != right:
                    raise QualityCacheError(
                        f"Flow 资产清单 Episode {episode_id} 的 {field} 与任务登记不一致"
                    )
        manifest_digest = canonical_json_sha256(manifest)
        expected_digest = str(job.get("asset_manifest_sha256") or "")
        if expected_digest and expected_digest != manifest_digest:
            raise QualityCacheError("Flow 返回的资产清单摘要不一致")

        published_manifest = source_root / "asset_manifest.json"
        try:
            stored_manifest = json.loads(published_manifest.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise QualityCacheError("NAS 资产尚未发布 asset_manifest.json") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise QualityCacheError("NAS asset_manifest.json 无法读取") from exc
        if canonical_json_sha256(stored_manifest) != manifest_digest:
            raise QualityCacheError("NAS 资产清单与 Flow 登记内容不一致")

        specs = []
        seen = set()
        for episode in (
            manifest_episodes[episode_id] for episode_id in platform_episodes
        ):
            files = (episode.get("manifest") or {}).get("files") or []
            if not files:
                raise QualityCacheError(
                    f"Episode {episode.get('episode_id') or '?'} 缺少逐文件清单"
                )
            for item in files:
                relative = self._safe_relative_path(
                    item.get("relative_path"), "资产清单文件路径"
                )
                normalized = relative.as_posix()
                if normalized in seen:
                    raise QualityCacheError(f"资产清单包含重复文件：{normalized}")
                seen.add(normalized)
                try:
                    expected_size = int(item["size_bytes"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise QualityCacheError(f"资产清单文件大小无效：{normalized}") from exc
                expected_sha256 = str(item.get("sha256") or "").lower()
                if expected_size < 0 or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
                    raise QualityCacheError(f"资产清单文件校验信息无效：{normalized}")
                source = source_root / relative
                try:
                    source.resolve().relative_to(source_root.resolve())
                except (OSError, ValueError) as exc:
                    raise QualityCacheError(f"NAS 清单文件超出资产根目录：{normalized}") from exc
                if not source.is_file():
                    raise QualityCacheError(f"NAS 缺少清单文件：{normalized}")
                if source.stat().st_size != expected_size:
                    raise QualityCacheError(f"NAS 文件大小与清单不一致：{normalized}")
                specs.append(
                    {
                        "relative_path": normalized,
                        "size_bytes": expected_size,
                        "sha256": expected_sha256,
                    }
                )

        primary_paths = {
            (
                self._safe_relative_path(item["relative_path"], "Episode 相对目录")
                / self._safe_relative_path(item["primary_file"], "Episode 主文件")
            ).as_posix()
            for item in job.get("episodes") or []
        }
        missing_primaries = sorted(primary_paths - seen)
        if missing_primaries:
            raise QualityCacheError(
                f"资产清单缺少 Episode 主文件：{', '.join(missing_primaries)}"
            )

        specs.append(
            {
                "relative_path": "asset_manifest.json",
                "size_bytes": published_manifest.stat().st_size,
                "sha256": sha256_file(published_manifest),
            }
        )
        return sorted(specs, key=lambda item: item["relative_path"]), manifest_digest

    @staticmethod
    def _verify_manifest_files(
        asset_root: Path,
        files: list[dict],
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, str]:
        verified_files: dict[str, str] = {}
        total_files = len(files)
        for index, item in enumerate(files, start=1):
            relative = Path(item["relative_path"])
            target = asset_root / relative
            if not target.is_file() or target.stat().st_size != int(item["size_bytes"]):
                raise QualityCacheError(f"缓存文件大小校验失败：{relative}")
            digest = sha256_file(target)
            if digest != item["sha256"]:
                target.unlink(missing_ok=True)
                raise QualityCacheError(f"缓存文件 SHA-256 校验失败：{relative}")
            relative_path = relative.as_posix()
            verified_files[relative_path] = digest
            QualityCacheManager._emit(
                progress_callback,
                {
                    "status": "verifying",
                    "phase": "verifying",
                    "progress": 99,
                    "verified_files": index,
                    "total_files": total_files,
                    "current_file": relative_path,
                },
            )
        return verified_files

    def _reuse_ready_cache(
        self,
        client: FlowClient,
        job: dict,
        ready_job_root: Path,
        ready_asset_root: Path,
        *,
        total_bytes: int,
        files: list[dict],
        asset_manifest_sha256: str,
    ) -> dict | None:
        if not ready_job_root.exists():
            return None
        state_path = ready_job_root / ".qc-cache.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise QualityCacheError(f"已有本地缓存状态损坏，请人工检查：{ready_job_root}")
        if state.get("schema_version") == 3 and not state.get("cache_complete"):
            # Version 3 keeps a durable ready root while remaining Episodes are
            # still copied in the background. It is resumable, not replaceable.
            return None
        matches = (
            state.get("job_code") == job.get("code")
            and state.get("asset_id") == job.get("asset_id")
            and set(state.get("episode_ids") or [])
            == {item["episode_id"] for item in job.get("episodes", [])}
            and int(state.get("total_bytes") or 0) == total_bytes
            and state.get("asset_manifest_sha256") == asset_manifest_sha256
        )
        if matches:
            try:
                verified_files = self._verify_manifest_files(ready_asset_root, files)
                primary_files = self._verify_episode_primary_files(job, verified_files)
            except QualityCacheError:
                matches = False
        if matches:
            if state.get("schema_version") != 3:
                state = self._migrate_complete_cache_state(
                    state,
                    job,
                    files,
                    primary_files,
                    total_bytes=total_bytes,
                )
                self._write_json_atomic(state_path, state)
            response = self._report_cache_with_retry(
                client,
                str(job["code"]),
                status="cache_ready",
                cache_progress=100,
                cached_bytes=total_bytes,
                cache_workstation=self.workspace_name,
                state_path=state_path,
                state=state,
            )
            return {
                "job": response or job,
                "cache_dir": str(ready_asset_root),
                "primary_files": [str(ready_asset_root / item["path"]) for item in primary_files],
                "total_bytes": total_bytes,
                "cache_complete": True,
                "cached_episode_count": len(job.get("episodes") or []),
                "total_episode_count": len(job.get("episodes") or []),
                "reused": True,
            }
        if not state.get("result_synced"):
            raise QualityCacheError(f"已有未同步的本地缓存，禁止覆盖：{ready_job_root}")
        shutil.rmtree(ready_job_root)
        return None

    def _migrate_complete_cache_state(
        self,
        state: dict,
        job: dict,
        files: list[dict],
        primary_files: list[dict],
        *,
        total_bytes: int,
    ) -> dict:
        """Upgrade a verified v2 cache without losing local result metadata."""
        primary_by_episode = {
            str(item["episode_id"]): item for item in primary_files
        }
        episode_states = []
        for episode in job.get("episodes") or []:
            relative_path = self._safe_relative_path(
                episode["relative_path"], "Episode 相对目录"
            ).as_posix()
            prefix = f"{relative_path}/"
            episode_files = [
                item for item in files if item["relative_path"].startswith(prefix)
            ]
            episode_bytes = sum(int(item["size_bytes"]) for item in episode_files)
            episode_states.append(
                {
                    "episode_id": episode["episode_id"],
                    "relative_path": relative_path,
                    "files": episode_files,
                    "status": "ready",
                    "cached_bytes": episode_bytes,
                    "total_bytes": episode_bytes,
                    "error": "",
                    "primary_files": [
                        primary_by_episode[str(episode["episode_id"])]
                    ],
                }
            )
        migrated = dict(state)
        migrated.update(
            {
                "schema_version": 3,
                "episode_ids": [item["episode_id"] for item in job.get("episodes") or []],
                "episodes": episode_states,
                "asset_manifest_ready": True,
                "cache_complete": True,
                "cache_status": "cache_ready",
                "cached_bytes": total_bytes,
                "total_bytes": total_bytes,
                "cached_episode_count": len(episode_states),
                "total_episode_count": len(episode_states),
                "primary_files": primary_files,
                "asset_directory": self._asset_directory_name(job),
            }
        )
        return migrated

    def _verify_episode_primary_files(
        self, job: dict, verified_files: dict[str, str]
    ) -> list[dict]:
        episodes = job.get("episodes") or []
        if not episodes:
            raise QualityCacheError("质检任务不包含 Episode")
        verified = []
        for episode in episodes:
            relative_dir = self._safe_relative_path(
                episode["relative_path"], "Episode 相对目录"
            )
            primary_name = self._safe_relative_path(
                episode["primary_file"], "Episode 主文件"
            )
            primary_relative = relative_dir / primary_name
            primary_path = primary_relative.as_posix()
            actual_sha256 = verified_files.get(primary_path)
            if actual_sha256 is None:
                raise QualityCacheError(f"缓存缺少主文件：{primary_relative}")
            expected_sha256 = str(episode.get("checksum_sha256") or "")
            if expected_sha256 and actual_sha256 != expected_sha256:
                raise QualityCacheError(
                    f"Episode {episode['episode_id']} 主文件 SHA-256 校验失败"
                )
            verified.append(
                {
                    "episode_id": episode["episode_id"],
                    "path": primary_path,
                    "sha256": expected_sha256,
                }
            )
        return verified

    @staticmethod
    def _existing_bytes(partial_root: Path, files: list[dict]) -> int:
        copied = 0
        for item in files:
            target = partial_root / item["relative_path"]
            partial = target.with_name(target.name + ".partial")
            expected_size = int(item["size_bytes"])
            if target.is_file() and target.stat().st_size == expected_size:
                if sha256_file(target) == item["sha256"]:
                    copied += target.stat().st_size
                else:
                    target.unlink()
            elif partial.is_file() and partial.stat().st_size <= expected_size:
                copied += partial.stat().st_size
        return copied

    def _copy_resumable(
        self,
        source: Path,
        target: Path,
        *,
        copied_bytes: int,
        total_bytes: int,
        callback: ProgressCallback,
        expected_size: int,
    ) -> int:
        source_size = source.stat().st_size
        if source_size != expected_size:
            raise QualityCacheError(f"NAS 文件在下载前发生变化：{source.name}")
        if target.is_file() and target.stat().st_size == source_size:
            return copied_bytes
        partial = target.with_name(target.name + ".partial")
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > source_size:
            partial.unlink()
            offset = 0
        mode = "ab" if offset else "wb"
        with source.open("rb") as input_file, partial.open(mode) as output_file:
            input_file.seek(offset)
            while True:
                chunk = input_file.read(self.chunk_size)
                if not chunk:
                    break
                output_file.write(chunk)
                copied_bytes += len(chunk)
                callback(
                    {
                        "status": "caching",
                        "progress": int(copied_bytes * 100 / total_bytes) if total_bytes else 100,
                        "cached_bytes": copied_bytes,
                        "current_file": str(source),
                    }
                )
        if partial.stat().st_size != source_size:
            raise QualityCacheError(f"缓存文件大小校验失败：{source.name}")
        os.replace(partial, target)
        return copied_bytes

    @staticmethod
    def _write_json_atomic(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial")
        # Write the exact UTF-8 bytes used by result SHA-256 calculation.
        # Text-mode writes translate LF to CRLF on Windows, which made the
        # uploaded result differ from the digest calculated before writing.
        temporary.write_bytes(
            (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        os.replace(temporary, path)

    @staticmethod
    def _emit(callback: ProgressCallback | None, state: dict[str, object]) -> None:
        if callback:
            callback(state)
