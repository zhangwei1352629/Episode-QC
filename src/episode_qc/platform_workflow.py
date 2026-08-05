"""Flow integration and full local staging for large QC source datasets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable

from episode_qc.source_paths import resolve_source_directory


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


class FlowClient:
    def __init__(self, base_url: str, username: str, password: str, *, timeout: int = 30):
        self.base_url = str(base_url).strip().rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise FlowClientError("Flow 地址必须以 http:// 或 https:// 开头")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
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
                payload = json.loads(message)
                message = payload.get("detail") or payload
            except json.JSONDecodeError:
                pass
            raise FlowClientError(f"Flow 请求失败（HTTP {error.code}）：{message}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise FlowClientError(f"无法连接 Flow：{error}") from error

    def jobs(self, statuses: list[str] | None = None) -> list[dict]:
        suffix = ""
        if statuses:
            suffix = "?status=" + ",".join(statuses)
        return self.request("GET", f"/api/v1/qc/jobs{suffix}")["jobs"]

    def claim(self, job_code: str) -> dict:
        return self.request("POST", f"/api/v1/qc/jobs/{job_code}/claim", {})

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
        result_nas_path: str = "",
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
            normalized_results.append(normalized)
        payload = {
            "episode_results": normalized_results,
            "result": result or {},
            "result_nas_path": result_nas_path,
        }
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
        files = sorted(path for path in source.rglob("*") if path.is_file())
        if not files:
            raise QualityCacheError(f"NAS 资产目录没有文件：{source}")
        total_bytes = sum(path.stat().st_size for path in files)
        expected_bytes = int(claimed.get("asset_size_bytes") or 0)
        if expected_bytes and total_bytes < expected_bytes:
            raise QualityCacheError(
                f"NAS 文件不完整：实际 {total_bytes} 字节，小于清单 {expected_bytes} 字节"
            )
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
        )
        if reused is not None:
            return reused
        self._ensure_disk_space(total_bytes)
        partial_root.mkdir(parents=True, exist_ok=True)
        copied_bytes = self._existing_bytes(partial_root, source, files)
        client.report_cache(
            job_code,
            status="caching",
            cache_progress=min(99, int(copied_bytes * 100 / total_bytes)) if total_bytes else 0,
            cached_bytes=copied_bytes,
            cache_workstation=self.workspace_name,
        )
        last_reported_percent = -1
        for source_file in files:
            relative = source_file.relative_to(source)
            target = partial_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            copied_bytes = self._copy_resumable(
                source_file,
                target,
                copied_bytes=copied_bytes,
                total_bytes=total_bytes,
                callback=lambda state: self._emit(progress_callback, state),
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

        primary_files = self._verify_episode_primary_files(claimed, partial_root)

        state = {
            "schema_version": 2,
            "job_code": job_code,
            "asset_id": claimed["asset_id"],
            "episode_ids": [item["episode_id"] for item in claimed.get("episodes", [])],
            "source_uri": claimed["source_uri"],
            "total_bytes": total_bytes,
            "primary_files": primary_files,
            "result_synced": False,
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
            "job_code": job_code,
            "asset_id": job["asset_id"],
            "episode_results": normalized_results,
            "result": result or {},
        }
        local_results = self.cache_root / "results-pending" / job_code
        local_results.mkdir(parents=True, exist_ok=True)
        local_result = local_results / "qc_result.json"
        local_result.write_text(
            json.dumps(result_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result_nas_path = self._publish_result(job, local_result)
        if hasattr(client, "report_work"):
            client.report_work(job_code, action="heartbeat")
        response = client.submit_result(
            job_code,
            episode_results=episode_results,
            result=result or {},
            result_nas_path=result_nas_path,
        )
        state["result_synced"] = True
        state["result_nas_path"] = result_nas_path
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        shutil.rmtree(local_results)
        return response

    def record_local_episodes(self, job_code: str, mappings: list[dict]) -> None:
        state_path = self._state_path(job_code)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["local_episodes"] = mappings
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

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

    def _publish_result(self, job: dict, local_result: Path) -> str:
        asset_root = resolve_source_directory(str(job["asset_nas_uri"]))
        destination = (
            asset_root
            / "qc"
            / f"v{int(job.get('version') or 1)}"
            / "qc_result.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".qc-result-", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            shutil.copy2(local_result, temporary_path)
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        return str(destination)

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

    def _reuse_ready_cache(
        self,
        client: FlowClient,
        job: dict,
        ready_job_root: Path,
        ready_asset_root: Path,
        *,
        total_bytes: int,
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
        )
        if matches:
            try:
                primary_files = self._verify_episode_primary_files(job, ready_asset_root)
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

    def _verify_episode_primary_files(self, job: dict, asset_root: Path) -> list[dict]:
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
            primary = asset_root / primary_relative
            if not primary.is_file():
                raise QualityCacheError(f"缓存缺少主文件：{primary_relative}")
            expected_sha256 = str(episode.get("checksum_sha256") or "")
            if expected_sha256 and sha256_file(primary) != expected_sha256:
                raise QualityCacheError(
                    f"Episode {episode['episode_id']} 主文件 SHA-256 校验失败"
                )
            verified.append(
                {
                    "episode_id": episode["episode_id"],
                    "path": str(primary_relative),
                    "sha256": expected_sha256,
                }
            )
        return verified

    @staticmethod
    def _existing_bytes(partial_root: Path, source_root: Path, files: list[Path]) -> int:
        copied = 0
        for source in files:
            target = partial_root / source.relative_to(source_root)
            partial = target.with_name(target.name + ".partial")
            if target.is_file() and target.stat().st_size == source.stat().st_size:
                copied += target.stat().st_size
            elif partial.is_file() and partial.stat().st_size <= source.stat().st_size:
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
    ) -> int:
        source_size = source.stat().st_size
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
    def _emit(callback: ProgressCallback | None, state: dict[str, object]) -> None:
        if callback:
            callback(state)
