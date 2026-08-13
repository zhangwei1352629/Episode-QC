"""Flow integration and full local staging for large QC source datasets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
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
_FLOW_OPTIONAL_ANNOTATION_TEXT_FIELDS = {
    "target_key",
    "severity",
    "action",
    "comment",
}


def normalize_flow_annotation(annotation: dict) -> dict:
    """Translate one QC workspace annotation to Flow's public fact contract."""

    if not isinstance(annotation, dict):
        raise QualityCacheError("标注必须是对象")
    normalized = {
        field: annotation[field]
        for field in _FLOW_ANNOTATION_FIELDS
        if field in annotation
    }
    annotation_id = annotation.get("annotation_id", annotation.get("id"))
    if annotation_id is not None:
        normalized["id"] = str(annotation_id)
    for field in _FLOW_OPTIONAL_ANNOTATION_TEXT_FIELDS:
        if field in normalized and normalized[field] is None:
            normalized[field] = ""
    return normalized


def build_quality_fact_submission(job: dict, episode_results: list[dict]) -> tuple[dict | None, list[dict]]:
    """Return Flow fact fields, refusing local labels that are not task snapshots."""

    has_annotations = any("annotations" in item for item in episode_results)
    if not has_annotations:
        return None, [dict(item) for item in episode_results]
    missing_reference = [
        field for field in _FLOW_LABEL_REFERENCE_FIELDS if not str(job.get(field) or "").strip()
    ]
    if missing_reference:
        raise QualityCacheError(
            "Flow 质检任务缺少冻结标签库引用，不能提交标注："
            + "、".join(missing_reference)
        )
    label_set = {field: str(job[field]).strip() for field in _FLOW_LABEL_REFERENCE_FIELDS}
    normalized_results = []
    for episode_result in episode_results:
        normalized = dict(episode_result)
        if "annotations" not in normalized:
            raise QualityCacheError("版本化标签提交必须为每个 Episode 提供 annotations")
        annotations = normalized["annotations"]
        if not isinstance(annotations, list):
            raise QualityCacheError("Episode annotations 必须是列表")
        if int(normalized.get("annotation_count") or 0) != len(annotations):
            raise QualityCacheError("annotation_count 必须等于 annotations 数量")
        normalized_annotations = []
        for annotation in annotations:
            if not isinstance(annotation, dict):
                raise QualityCacheError("标注必须是对象")
            for field in ("label_set_key", "label_schema_version"):
                expected = label_set[
                    "label_set_id" if field == "label_set_key" else field
                ]
                actual = annotation.get(field)
                if actual is not None and str(actual) != expected:
                    raise QualityCacheError("本地标注标签库与 Flow 任务冻结版本不一致")
            normalized_annotations.append(normalize_flow_annotation(annotation))
        normalized["annotations"] = normalized_annotations
        normalized_results.append(normalized)
    return label_set, normalized_results


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
        label_set: dict | None = None,
        result: dict | None = None,
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
                annotations = episode_result["annotations"]
                if not isinstance(annotations, list):
                    raise FlowClientError("Episode annotations 必须是列表")
                normalized["annotations"] = [
                    normalize_flow_annotation(annotation) for annotation in annotations
                ]
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
            payload["label_set"] = dict(label_set)
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
    ) -> dict:
        job_code = self._safe_component(job["code"], "质检任务编号")
        claimed = client.claim(job_code)
        source = resolve_source_directory(str(claimed["source_uri"]))
        files, asset_manifest_sha256 = self._manifest_file_specs(claimed, source)
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
            return reused
        try:
            self.evict_expired()
        except Exception:
            pass
        self._ensure_disk_space(total_bytes)
        partial_root.mkdir(parents=True, exist_ok=True)
        copied_bytes = self._existing_bytes(partial_root, files)
        client.report_cache(
            job_code,
            status="caching",
            cache_progress=min(99, int(copied_bytes * 100 / total_bytes)) if total_bytes else 0,
            cached_bytes=copied_bytes,
            cache_workstation=self.workspace_name,
        )
        last_reported_percent = -1
        for file_spec in files:
            relative = Path(file_spec["relative_path"])
            source_file = source / relative
            target = partial_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            copied_bytes = self._copy_resumable(
                source_file,
                target,
                copied_bytes=copied_bytes,
                total_bytes=total_bytes,
                callback=lambda state: self._emit(progress_callback, state),
                expected_size=int(file_spec["size_bytes"]),
            )
            percent = min(99, int(copied_bytes * 100 / total_bytes)) if total_bytes else 99
            if percent >= last_reported_percent + 5:
                client.report_cache(
                    job_code,
                    status="caching",
                    cache_progress=percent,
                    cached_bytes=copied_bytes,
                    cache_workstation=self.workspace_name,
                )
                last_reported_percent = percent

        verified_files = self._verify_manifest_files(
            partial_root, files, progress_callback=progress_callback
        )
        primary_files = self._verify_episode_primary_files(claimed, verified_files)

        state = {
            "schema_version": 2,
            "job_code": job_code,
            "asset_id": claimed["asset_id"],
            "episode_ids": [item["episode_id"] for item in claimed.get("episodes", [])],
            "source_uri": claimed["source_uri"],
            "asset_manifest_sha256": asset_manifest_sha256,
            "total_bytes": total_bytes,
            "primary_files": primary_files,
            "result_synced": False,
            "result_upload_uri": claimed.get("result_upload_uri", ""),
            "result_staging_uri": claimed.get("result_staging_uri", ""),
            "next_attempt": int(claimed.get("next_attempt") or 1),
        }
        state["asset_directory"] = asset_directory
        (partial_job_root / ".qc-cache.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        ready_job_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial_job_root, ready_job_root)
        response = client.report_cache(
            job_code,
            status="cache_ready",
            cache_progress=100,
            cached_bytes=total_bytes,
            cache_workstation=self.workspace_name,
        )
        self._emit(
            progress_callback,
            {"status": "cache_ready", "progress": 100, "cached_bytes": total_bytes},
        )
        return {
            "job": response,
            "cache_dir": str(ready_root),
            "primary_files": [str(ready_root / item["path"]) for item in primary_files],
            "total_bytes": total_bytes,
            "reused": False,
        }

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
        pending_result = state.get("pending_result") or {}
        local_results = self.cache_root / "results-pending" / job_code
        local_result = local_results / "qc_result.json"
        if pending_result:
            if not local_result.is_file():
                raise QualityCacheError("待同步质检结果文件缺失，禁止重建或覆盖")
            try:
                result_document = json.loads(local_result.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise QualityCacheError("待同步质检结果文件无法读取，禁止重建或覆盖") from exc
            if not isinstance(result_document, dict):
                raise QualityCacheError("待同步质检结果格式无效，禁止重建或覆盖")
            result_id = str(result_document.get("result_id") or "")
            if (
                not result_id
                or result_id != str(pending_result.get("result_id") or "")
                or result_document.get("job_code") != job_code
                or result_document.get("asset_id") != job.get("asset_id")
            ):
                raise QualityCacheError("待同步质检结果与当前任务不一致，禁止重建或覆盖")
            try:
                attempt = int(result_document.get("attempt"))
            except (TypeError, ValueError) as exc:
                raise QualityCacheError("待同步质检结果缺少有效尝试序号") from exc
            if attempt < 1 or attempt != int(pending_result.get("attempt") or attempt):
                raise QualityCacheError("待同步质检结果尝试序号不一致，禁止重建或覆盖")
            result_sha256 = sha256_file(local_result)
            if result_sha256 != str(pending_result.get("result_sha256") or ""):
                raise QualityCacheError("待同步质检结果 SHA-256 不一致，禁止重建或覆盖")
            episode_results = result_document.get("episode_results")
            label_set = result_document.get("label_set")
            result_payload = result_document.get("result") or {}
            if not isinstance(episode_results, list) or not isinstance(result_payload, dict):
                raise QualityCacheError("待同步质检结果格式无效，禁止重建或覆盖")
            if label_set is not None and not isinstance(label_set, dict):
                raise QualityCacheError("待同步质检标签库引用格式无效，禁止重建或覆盖")
            created_at = str(pending_result.get("created_at") or "")
            if not created_at:
                raise QualityCacheError("待同步质检结果缺少创建时间，禁止重建或覆盖")
        else:
            label_set, episode_results = build_quality_fact_submission(job, episode_results)
            result_id = f"QCR-{uuid.uuid4().hex}"
            attempt = int(state.get("next_attempt") or job.get("next_attempt") or 1)
            created_at = datetime.now(timezone.utc).isoformat()
            result_payload = result or {}
        expected_ids = {item["episode_id"] for item in job.get("episodes", [])}
        submitted_ids = {item.get("episode_id") for item in episode_results}
        if len(submitted_ids) != len(episode_results) or submitted_ids != expected_ids:
            raise QualityCacheError("必须一次提交资产内全部且不重复的 Episode 质检结论")
        for episode_result in episode_results:
            decision = episode_result.get("decision")
            quality_grade = episode_result.get("quality_grade")
            if decision not in DECISION_MAP:
                raise QualityCacheError(f"不支持的质检结论：{decision}")
            if quality_grade not in {None, "excellent", "good", "medium", "poor", "invalid"}:
                raise QualityCacheError(f"不支持的质量等级：{quality_grade}")
            if int(episode_result.get("annotation_count") or 0) < 0:
                raise QualityCacheError("标注数量不能为负数")
        if not pending_result:
            normalized_results = [
                {
                    **item,
                    "decision": DECISION_MAP[item["decision"]],
                    "annotation_count": int(item.get("annotation_count") or 0),
                }
                for item in episode_results
            ]
            result_document = {
                "schema_version": 2,
                "result_id": result_id,
                "job_code": job_code,
                "asset_id": job["asset_id"],
                "attempt": attempt,
                "source_manifest_sha256": state.get("asset_manifest_sha256", ""),
                "episode_results": normalized_results,
                "result": result_payload,
            }
            if label_set is not None:
                result_document["label_set"] = label_set
            local_results.mkdir(parents=True, exist_ok=True)
            self._write_json_atomic(local_result, result_document)
            result_sha256 = sha256_file(local_result)
        result_manifest = {
            "schema_version": 1,
            "result_id": result_id,
            "result_sha256": result_sha256,
            "job_code": job_code,
            "asset_id": job["asset_id"],
            "attempt": attempt,
            "source_manifest_sha256": result_document.get(
                "source_manifest_sha256", state.get("asset_manifest_sha256", "")
            ),
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
            episode_results=episode_results,
            label_set=label_set,
            result=result_payload,
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

    def local_episode_mappings(self, job_code: str) -> list[dict]:
        state_path = self._state_path(job_code)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return list(state.get("local_episodes") or [])

    def _state_path(self, job_code: str) -> Path:
        safe_code = self._safe_component(job_code, "质检任务编号")
        state_path = self.cache_root / "ready" / safe_code / ".qc-cache.json"
        if not state_path.is_file():
            raise QualityCacheError("质检任务尚未完整缓存到本地")
        return state_path

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
            response = client.report_cache(
                str(job["code"]),
                status="cache_ready",
                cache_progress=100,
                cached_bytes=total_bytes,
                cache_workstation=self.workspace_name,
            )
            return {
                "job": response,
                "cache_dir": str(ready_asset_root),
                "primary_files": [str(ready_asset_root / item["path"]) for item in primary_files],
                "total_bytes": total_bytes,
                "reused": True,
            }
        if not state.get("result_synced"):
            raise QualityCacheError(f"已有未同步的本地缓存，禁止覆盖：{ready_job_root}")
        shutil.rmtree(ready_job_root)
        return None

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
