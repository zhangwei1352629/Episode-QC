from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable

import yaml
from mcap.reader import make_reader

from episode_qc.bvh import read_bvh_header
from episode_qc.platform_workflow import canonical_json_sha256
from episode_qc.source_paths import resolve_source_directory


SCHEMA_VERSION = 2


class WorkspaceConflictError(RuntimeError):
    """Raised when a browser tries to overwrite a record changed in another tab."""


APP_VERSION = "1.0.0"
VALID_SCOPES = {"episode", "time_range", "time_point"}
VALID_TARGETS = {"global", "mocap", "joint", "camera", "stream", "retarget", "robot", "hand"}
VALID_REVIEW_STATUSES = {"unreviewed", "in_progress", "completed", "needs_recheck", "reviewed"}
VALID_QUALITY_DECISIONS = {"pass", "pass_with_labels", "trim", "repair", "recollect", "reject"}
RESERVED_SHORTCUTS = {
    "SPACE",
    "ARROWLEFT",
    "ARROWRIGHT",
    "I",
    "O",
    "ENTER",
    "ESCAPE",
    "N",
    "P",
    "F",
}
LABEL_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEFAULT_SEVERITY_LEVELS = [
    {"code": "minor", "name": "轻微", "order": 1},
    {"code": "normal", "name": "一般", "order": 2},
    {"code": "critical", "name": "严重", "order": 3},
]
DEFAULT_LABEL_ACTIONS = [
    {"code": "keep", "name": "保留"},
    {"code": "keep_with_label", "name": "保留但标记"},
    {"code": "trim", "name": "裁剪区间"},
    {"code": "repair", "name": "需要修复"},
    {"code": "recollect", "name": "需要重采"},
    {"code": "reject", "name": "整条废弃"},
    {"code": "review", "name": "待复核"},
]
SIMPLE_SCOPE_ALIASES = {
    "区间": ("time_range",),
    "时间段": ("time_range",),
    "时间范围": ("time_range",),
    "time_range": ("time_range",),
    "时间点": ("time_point",),
    "单点": ("time_point",),
    "time_point": ("time_point",),
    "整条": ("episode",),
    "整段": ("episode",),
    "整个episode": ("episode",),
    "episode": ("episode",),
    "全部": ("time_range", "time_point", "episode"),
}
SIMPLE_TARGET_ALIASES = {
    "全局": ("global",),
    "默认": ("global",),
    "global": ("global",),
    "画面": ("camera",),
    "相机": ("camera",),
    "camera": ("camera",),
    "动作": ("mocap",),
    "动捕": ("mocap",),
    "mocap": ("mocap",),
    "关节": ("joint",),
    "joint": ("joint",),
    "数据流": ("stream",),
    "stream": ("stream",),
    "机器人": ("robot",),
    "robot": ("robot",),
    "手部": ("hand",),
    "hand": ("hand",),
    "全部": ("global", "camera", "mocap", "joint"),
}
SIMPLE_SEVERITY_ALIASES = {
    "轻微": "minor", "minor": "minor",
    "一般": "normal", "普通": "normal", "normal": "normal",
    "严重": "critical", "critical": "critical",
}
SIMPLE_ACTION_ALIASES = {
    "保留": "keep", "keep": "keep",
    "保留但标记": "keep_with_label", "标记后保留": "keep_with_label",
    "keep_with_label": "keep_with_label",
    "裁剪": "trim", "裁剪区间": "trim", "trim": "trim",
    "修复": "repair", "需要修复": "repair", "repair": "repair",
    "重采": "recollect", "需要重采": "recollect", "recollect": "recollect",
    "废弃": "reject", "整条废弃": "reject", "reject": "reject",
    "复核": "review", "待复核": "review", "review": "review",
}
SIMPLE_GROUP_COLORS = (
    "#3B82F6", "#F59E0B", "#8B5CF6", "#10B981", "#F97316", "#64748B"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("\0".join(map(str, parts)).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def connect_workspace(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_workspace(
    db_path: str | Path,
    *,
    name: str = "Mocap QC 工作区",
    reviewer_name: str = "",
) -> dict[str, object]:
    with connect_workspace(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspace (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                reviewer_name TEXT NOT NULL DEFAULT '',
                active_label_set_id TEXT,
                settings_json TEXT NOT NULL DEFAULT '{}',
                schema_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS qc_task (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspace(id),
                task_code TEXT NOT NULL UNIQUE,
                task_name TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT 'local',
                flow_job_code TEXT UNIQUE,
                asset_id TEXT,
                source_uri TEXT NOT NULL,
                local_source_path TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'server_path',
                status TEXT NOT NULL DEFAULT 'importing',
                import_error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                last_episode_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS data_source (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspace(id),
                task_id TEXT REFERENCES qc_task(id),
                root_path TEXT NOT NULL UNIQUE,
                profile_id TEXT,
                profile_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_scanned_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episode (
                id TEXT PRIMARY KEY,
                data_source_id TEXT NOT NULL REFERENCES data_source(id),
                relative_path TEXT NOT NULL,
                episode_name TEXT NOT NULL,
                data_group TEXT NOT NULL,
                mcap_path TEXT NOT NULL,
                summary_path TEXT,
                config_path TEXT,
                fingerprint TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_mtime_ns INTEGER NOT NULL,
                start_time_ns INTEGER,
                end_time_ns INTEGER,
                duration_ns INTEGER,
                import_status TEXT NOT NULL,
                import_error TEXT,
                cache_status TEXT NOT NULL DEFAULT 'not_prepared',
                review_status TEXT NOT NULL DEFAULT 'unreviewed',
                quality_decision TEXT,
                reviewer_name TEXT,
                last_playhead_ns INTEGER NOT NULL DEFAULT 0,
                annotation_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reviewed_at TEXT,
                UNIQUE(data_source_id, relative_path)
            );

            CREATE TABLE IF NOT EXISTS stream (
                id TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
                topic TEXT NOT NULL,
                stream_key TEXT,
                stream_type TEXT NOT NULL,
                display_name TEXT NOT NULL,
                encoding TEXT,
                schema_name TEXT,
                adapter_id TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                first_time_ns INTEGER,
                last_time_ns INTEGER,
                nominal_hz REAL,
                available INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(episode_id, topic)
            );

            CREATE TABLE IF NOT EXISTS label_set (
                id TEXT PRIMARY KEY,
                label_set_key TEXT NOT NULL,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                language TEXT NOT NULL,
                source_format TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                raw_schema_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(label_set_key, version)
            );

            CREATE TABLE IF NOT EXISTS label_definition (
                id TEXT PRIMARY KEY,
                label_set_id TEXT NOT NULL REFERENCES label_set(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                group_code TEXT NOT NULL,
                description TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                scopes_json TEXT NOT NULL,
                targets_json TEXT NOT NULL,
                default_severity TEXT,
                default_action TEXT,
                shortcut TEXT,
                color TEXT,
                applicable_profiles_json TEXT NOT NULL DEFAULT '[]',
                fields_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(label_set_id, code)
            );

            CREATE TABLE IF NOT EXISTS annotation (
                id TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL REFERENCES episode(id),
                label_set_key TEXT NOT NULL,
                label_schema_version TEXT NOT NULL,
                label_code TEXT NOT NULL,
                scope TEXT NOT NULL,
                start_offset_ns INTEGER NOT NULL,
                end_offset_ns INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                target_key TEXT,
                severity TEXT,
                action TEXT,
                comment TEXT,
                attributes_json TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'confirmed',
                reviewer_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS change_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                session_id TEXT NOT NULL,
                undone INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS export_record (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspace(id),
                filters_json TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                episode_count INTEGER NOT NULL,
                annotation_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_episode_source ON episode(data_source_id);
            CREATE INDEX IF NOT EXISTS idx_task_workspace ON qc_task(workspace_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_episode_review ON episode(review_status, quality_decision);
            CREATE INDEX IF NOT EXISTS idx_stream_episode ON stream(episode_id);
            CREATE INDEX IF NOT EXISTS idx_annotation_episode ON annotation(episode_id, start_offset_ns);
            CREATE INDEX IF NOT EXISTS idx_annotation_label ON annotation(label_code);
            CREATE INDEX IF NOT EXISTS idx_change_session ON change_log(session_id, id);
            """
        )
        _ensure_column(connection, "data_source", "task_id", "TEXT REFERENCES qc_task(id)")
        _ensure_column(connection, "qc_task", "source_type", "TEXT NOT NULL DEFAULT 'server_path'")
        row = connection.execute("SELECT * FROM workspace LIMIT 1").fetchone()
        if row is None:
            workspace_id = _new_id("ws")
            now = _now()
            connection.execute(
                "INSERT INTO workspace VALUES (?, ?, ?, NULL, '{}', ?, ?, ?)",
                (workspace_id, name, reviewer_name, SCHEMA_VERSION, now, now),
            )
        _migrate_data_sources_to_tasks(connection)
        _upgrade_inferred_platform_tasks(connection)
        _migrate_last_episode_to_task(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_data_source_task ON data_source(task_id) WHERE task_id IS NOT NULL"
        )
        connection.execute("UPDATE workspace SET schema_version = ?", (SCHEMA_VERSION,))
        row = connection.execute("SELECT * FROM workspace LIMIT 1").fetchone()
        return dict(row)


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _migrate_data_sources_to_tasks(connection: sqlite3.Connection) -> None:
    workspace = connection.execute("SELECT id FROM workspace LIMIT 1").fetchone()
    if workspace is None:
        return
    sources = connection.execute(
        "SELECT * FROM data_source WHERE task_id IS NULL ORDER BY created_at, id"
    ).fetchall()
    for source in sources:
        task_id = _stable_id("tsk", source["id"])
        created_at = source["created_at"] or _now()
        date_code = str(created_at)[:10].replace("-", "") or "UNKNOWN"
        task_code = f"LOCAL-{date_code}-{task_id[-6:].upper()}"
        root_path = str(source["root_path"])
        task_name = Path(root_path).name or root_path.rstrip("/").rsplit("/", 1)[-1] or task_code
        connection.execute(
            """
            INSERT OR IGNORE INTO qc_task(
                id, workspace_id, task_code, task_name, origin, flow_job_code, asset_id,
                source_uri, local_source_path, status, import_error, metadata_json,
                last_episode_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'local', NULL, NULL, ?, ?, 'ready', NULL, '{}', NULL, ?, ?)
            """,
            (
                task_id,
                workspace["id"],
                task_code,
                task_name,
                root_path,
                root_path,
                created_at,
                source["last_scanned_at"] or created_at,
            ),
        )
        connection.execute("UPDATE data_source SET task_id = ? WHERE id = ?", (task_id, source["id"]))
        _refresh_task_status(connection, task_id)


def _upgrade_inferred_platform_tasks(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT t.id, t.task_code, ds.root_path
        FROM qc_task t JOIN data_source ds ON ds.task_id = t.id
        WHERE t.origin = 'local' AND t.task_code LIKE 'LOCAL-%'
        """
    ).fetchall()
    for row in rows:
        parts = Path(str(row["root_path"])).parts
        try:
            ready_index = parts.index("ready")
            job_code = parts[ready_index + 1]
        except (ValueError, IndexError):
            continue
        if not re.fullmatch(r"QCJ-[A-Za-z0-9-]+", job_code):
            continue
        duplicate = connection.execute(
            "SELECT 1 FROM qc_task WHERE (task_code = ? OR flow_job_code = ?) AND id != ?",
            (job_code, job_code, row["id"]),
        ).fetchone()
        if duplicate:
            continue
        connection.execute(
            """
            UPDATE qc_task
            SET task_code = ?, task_name = ?, origin = 'flow', flow_job_code = ?, updated_at = ?
            WHERE id = ?
            """,
            (job_code, job_code, job_code, _now(), row["id"]),
        )


def _migrate_last_episode_to_task(connection: sqlite3.Connection) -> None:
    workspace = connection.execute("SELECT settings_json FROM workspace LIMIT 1").fetchone()
    last_episode_id = (_loads(workspace["settings_json"], {}) if workspace else {}).get("last_episode_id")
    if not last_episode_id:
        return
    row = connection.execute(
        """
        SELECT ds.task_id
        FROM episode e JOIN data_source ds ON ds.id = e.data_source_id
        WHERE e.id = ?
        """,
        (last_episode_id,),
    ).fetchone()
    if row and row["task_id"]:
        connection.execute(
            "UPDATE qc_task SET last_episode_id = COALESCE(last_episode_id, ?) WHERE id = ?",
            (last_episode_id, row["task_id"]),
        )


def update_workspace_settings(
    db_path: str | Path,
    *,
    reviewer_name: str | None = None,
    name: str | None = None,
    last_episode_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, object]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        row = connection.execute("SELECT * FROM workspace LIMIT 1").fetchone()
        settings = _loads(row["settings_json"], {})
        if last_episode_id is not None:
            episode = connection.execute(
                """
                SELECT e.id, ds.task_id
                FROM episode e JOIN data_source ds ON ds.id = e.data_source_id
                WHERE e.id = ?
                """,
                (last_episode_id,),
            ).fetchone()
            if not episode:
                raise KeyError(f"Episode 不存在: {last_episode_id}")
            if task_id is not None and episode["task_id"] != task_id:
                raise ValueError("Episode 不属于当前 QC 任务")
            settings["last_episode_id"] = last_episode_id
            connection.execute(
                "UPDATE qc_task SET last_episode_id = ?, updated_at = ? WHERE id = ?",
                (last_episode_id, _now(), episode["task_id"]),
            )
        connection.execute(
            "UPDATE workspace SET name = ?, reviewer_name = ?, settings_json = ?, updated_at = ? WHERE id = ?",
            (
                name if name is not None else row["name"],
                reviewer_name if reviewer_name is not None else row["reviewer_name"],
                _json(settings),
                _now(),
                row["id"],
            ),
        )
    return workspace_state(db_path, task_id=task_id)["workspace"]


def _load_profile(profile_path: str | Path | None) -> dict[str, object]:
    if not profile_path:
        return {}
    value = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("data Profile 顶层必须是对象")
    return value


def scan_data_source(
    db_path: str | Path,
    root_path: str | Path,
    *,
    profile_path: str | Path | None = None,
    task_code: str | None = None,
    task_name: str | None = None,
    origin: str = "local",
    flow_job_code: str | None = None,
    asset_id: str | None = None,
    source_uri: str | None = None,
    task_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    initialize_workspace(db_path)
    requested_root_path = str(root_path)
    root = resolve_source_directory(root_path)
    profile = _load_profile(profile_path)
    profile_id = str(((profile.get("profile") or {}) if isinstance(profile.get("profile"), dict) else {}).get("id") or "default_v1")
    profile_json = _json(profile)

    with connect_workspace(db_path) as connection:
        workspace = connection.execute("SELECT * FROM workspace LIMIT 1").fetchone()
        source_id = _stable_id("src", root)
        now = _now()
        existing_source = connection.execute(
            "SELECT id, task_id, profile_json FROM data_source WHERE root_path = ?",
            (str(root),),
        ).fetchone()
        profile_changed = bool(existing_source and existing_source["profile_json"] != profile_json)
        task_id = str(existing_source["task_id"]) if existing_source and existing_source["task_id"] else _stable_id("tsk", source_id)
        existing_task = connection.execute("SELECT * FROM qc_task WHERE id = ?", (task_id,)).fetchone()
        effective_task_code = str(task_code or (existing_task["task_code"] if existing_task else "")).strip()
        if not effective_task_code:
            effective_task_code = f"LOCAL-{now[:10].replace('-', '')}-{task_id[-6:].upper()}"
        effective_task_name = str(task_name or (existing_task["task_name"] if existing_task else "")).strip()
        if not effective_task_name:
            effective_task_name = root.name or effective_task_code
        effective_source_uri = str(
            source_uri or (existing_task["source_uri"] if existing_task else "") or requested_root_path
        )
        effective_origin = str(origin or (existing_task["origin"] if existing_task else "local"))
        metadata = task_metadata if task_metadata is not None else (
            _loads(existing_task["metadata_json"], {}) if existing_task else {}
        )
        connection.execute(
            """
            INSERT INTO qc_task(
                id, workspace_id, task_code, task_name, origin, flow_job_code, asset_id,
                source_uri, local_source_path, status, import_error, metadata_json,
                last_episode_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'importing', NULL, ?, NULL, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_code = excluded.task_code,
                task_name = excluded.task_name,
                origin = excluded.origin,
                flow_job_code = COALESCE(excluded.flow_job_code, qc_task.flow_job_code),
                asset_id = COALESCE(excluded.asset_id, qc_task.asset_id),
                source_uri = excluded.source_uri,
                local_source_path = excluded.local_source_path,
                status = 'importing',
                import_error = NULL,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                workspace["id"],
                effective_task_code,
                effective_task_name,
                effective_origin,
                flow_job_code,
                asset_id,
                effective_source_uri,
                str(root),
                _json(metadata or {}),
                existing_task["created_at"] if existing_task else now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO data_source(id, workspace_id, task_id, root_path, profile_id, profile_json, enabled, last_scanned_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(root_path) DO UPDATE SET
                task_id = excluded.task_id,
                profile_id = excluded.profile_id,
                profile_json = excluded.profile_json,
                enabled = 1,
                last_scanned_at = excluded.last_scanned_at
            """,
            (source_id, workspace["id"], task_id, str(root), profile_id, profile_json, now, now),
        )
        source = connection.execute("SELECT * FROM data_source WHERE root_path = ?", (str(root),)).fetchone()
        source_id = source["id"]

    candidates = _discover_episode_mcaps(root, profile)
    indexed: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    with connect_workspace(db_path) as connection:
        for mcap_path in candidates:
            relative_path = str(mcap_path.parent.relative_to(root)) if mcap_path.parent != root else mcap_path.parent.name
            seen_paths.add(relative_path)
            indexed.append(
                _index_episode(
                    connection,
                    source_id,
                    root,
                    relative_path,
                    mcap_path,
                    profile,
                    force_reindex=profile_changed,
                )
            )

        missing_rows = connection.execute("SELECT id, relative_path FROM episode WHERE data_source_id = ?", (source_id,)).fetchall()
        for row in missing_rows:
            if row["relative_path"] not in seen_paths:
                connection.execute(
                    "UPDATE episode SET import_status = 'source_missing', import_error = ?, updated_at = ? WHERE id = ?",
                    ("源文件在本次重扫中未找到", _now(), row["id"]),
                )

    failures = [item for item in indexed if item["import_status"] != "ready"]
    with connect_workspace(db_path) as connection:
        if not indexed:
            connection.execute(
                "UPDATE qc_task SET status = 'failed', import_error = ?, updated_at = ? WHERE id = ?",
                ("目录中没有识别到 Episode", _now(), task_id),
            )
        else:
            import_error = f"{len(failures)} 条 Episode 导入失败" if failures else None
            connection.execute(
                "UPDATE qc_task SET import_error = ?, updated_at = ? WHERE id = ?",
                (import_error, _now(), task_id),
            )
            _refresh_task_status(connection, task_id)
        task = _task_row(connection, task_id)
    return {
        "requested_root_path": requested_root_path,
        "root_path": str(root),
        "source_id": source_id,
        "task_id": task_id,
        "task": task,
        "existing_task": bool(existing_source),
        "profile_id": profile_id,
        "discovered": len(candidates),
        "ready": len(indexed) - len(failures),
        "failed": len(failures),
        "unchanged": sum(1 for item in indexed if item.get("unchanged")),
        "episodes": indexed,
    }


def _discover_episode_mcaps(root: Path, profile: dict[str, object]) -> list[Path]:
    import_config = profile.get("import") if isinstance(profile.get("import"), dict) else {}
    episode_patterns = list(import_config.get("episode_directory_patterns") or ["episode_*"])
    mcap_patterns = list(import_config.get("mcap_file_patterns") or ["episode.mcap", "*.mcap"])
    bvh_patterns = list(import_config.get("bvh_file_patterns") or ["motion.bvh", "*.bvh"])
    paths_by_directory: dict[Path, Path] = {}
    candidates = list(root.rglob("*.mcap")) + list(root.rglob("*.bvh"))
    for path in candidates:
        if not path.is_file():
            continue
        episode_match = any(fnmatch(path.parent.name, str(pattern)) for pattern in episode_patterns)
        patterns = bvh_patterns if path.suffix.lower() == ".bvh" else mcap_patterns
        file_match = any(fnmatch(path.name, str(pattern)) for pattern in patterns)
        if episode_match and file_match:
            resolved = path.resolve()
            current = paths_by_directory.get(resolved.parent)
            if current is None or _episode_file_rank(resolved) < _episode_file_rank(current):
                paths_by_directory[resolved.parent] = resolved
    return sorted(paths_by_directory.values(), key=lambda item: _natural_key(str(item.relative_to(root))))


def _episode_file_rank(path: Path) -> tuple[int, str]:
    preferred = {"episode.mcap": 0, "motion.bvh": 1}
    suffix_rank = 2 if path.suffix.lower() == ".mcap" else 3
    return preferred.get(path.name.lower(), suffix_rank), path.name.lower()


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def _index_episode(
    connection: sqlite3.Connection,
    source_id: str,
    root: Path,
    relative_path: str,
    mcap_path: Path,
    profile: dict[str, object],
    *,
    force_reindex: bool = False,
) -> dict[str, object]:
    episode_id = _stable_id("ep", source_id, relative_path)
    stat = mcap_path.stat()
    old = connection.execute("SELECT * FROM episode WHERE id = ?", (episode_id,)).fetchone()
    if (
        old
        and not force_reindex
        and old["import_status"] == "ready"
        and int(old["file_size"]) == stat.st_size
        and int(old["file_mtime_ns"]) == stat.st_mtime_ns
    ):
        return _existing_episode_index_result(connection, old, unchanged=True)

    summary_path = _first_existing(
        mcap_path.parent,
        ["metadata.json", "metadata.yaml", "summary.yaml", "episode_summary.yaml"],
    )
    config_path = _first_existing(mcap_path.parent, ["config_snapshot.yaml", "config.yaml"])
    streams: list[dict[str, object]] = []
    start_ns: int | None = None
    end_ns: int | None = None
    error: str | None = None

    try:
        if mcap_path.suffix.lower() == ".bvh":
            header = read_bvh_header(mcap_path)
            start_ns = 0
            frame_time_ns = round(header.frame_time_sec * 1_000_000_000)
            end_ns = header.frame_count * frame_time_ns
            duration_ns = end_ns
            streams.append(
                {
                    "id": _stable_id("str", episode_id, "/bvh/motion"),
                    "episode_id": episode_id,
                    "topic": "/bvh/motion",
                    "stream_key": "human_motion",
                    "stream_type": "mocap",
                    "display_name": "人体 BVH",
                    "encoding": "bvh",
                    "schema_name": "bvh.hierarchy_motion",
                    "adapter_id": "bvh_v1",
                    "message_count": header.frame_count,
                    "first_time_ns": 0 if header.frame_count else None,
                    "last_time_ns": (header.frame_count - 1) * frame_time_ns if header.frame_count else None,
                    "nominal_hz": 1.0 / header.frame_time_sec,
                    "available": 1 if header.frame_count else 0,
                    "metadata_json": _json(
                        {
                            "joint_names": [joint.name for joint in header.joints],
                            "parent_indices": [joint.parent_index for joint in header.joints],
                            "frame_time_sec": header.frame_time_sec,
                        }
                    ),
                }
            )
        else:
            with mcap_path.open("rb") as source:
                reader = make_reader(source)
                summary = reader.get_summary()
                if summary is None:
                    raise ValueError("MCAP 缺少 Summary")
                statistics = summary.statistics
                if statistics:
                    start_ns = statistics.message_start_time
                    end_ns = statistics.message_end_time
                    counts = statistics.channel_message_counts
                else:
                    counts = {}
                duration_ns = max(0, (end_ns or 0) - (start_ns or 0)) if start_ns is not None and end_ns is not None else None
                for channel_id, channel in sorted(summary.channels.items()):
                    schema = summary.schemas.get(channel.schema_id)
                    count = int(counts.get(channel_id, 0))
                    streams.append(
                        _stream_from_channel(
                            episode_id,
                            channel.topic,
                            channel.message_encoding,
                            schema.name if schema else "",
                            count,
                            start_ns,
                            end_ns,
                            duration_ns,
                            profile,
                        )
                    )
    except Exception as exc:
        duration_ns = None
        error = f"{type(exc).__name__}: {exc}"

    fingerprint = hashlib.sha256(
        _json([source_id, relative_path, stat.st_size, stat.st_mtime_ns, start_ns, end_ns]).encode("utf-8")
    ).hexdigest()
    data_group = root.name if Path(relative_path).parent == Path(".") else Path(relative_path).parts[0]
    now = _now()
    import_status = "ready" if error is None else "failed"

    cache_status = "stale" if old and old["fingerprint"] != fingerprint else "not_prepared"
    connection.execute(
            """
            INSERT INTO episode(
                id, data_source_id, relative_path, episode_name, data_group, mcap_path,
                summary_path, config_path, fingerprint, file_size, file_mtime_ns,
                start_time_ns, end_time_ns, duration_ns, import_status, import_error,
                cache_status, review_status, quality_decision, reviewer_name,
                last_playhead_ns, annotation_count, created_at, updated_at, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed', NULL, NULL, 0, 0, ?, ?, NULL)
            ON CONFLICT(data_source_id, relative_path) DO UPDATE SET
                episode_name = excluded.episode_name,
                data_group = excluded.data_group,
                mcap_path = excluded.mcap_path,
                summary_path = excluded.summary_path,
                config_path = excluded.config_path,
                fingerprint = excluded.fingerprint,
                file_size = excluded.file_size,
                file_mtime_ns = excluded.file_mtime_ns,
                start_time_ns = excluded.start_time_ns,
                end_time_ns = excluded.end_time_ns,
                duration_ns = excluded.duration_ns,
                import_status = excluded.import_status,
                import_error = excluded.import_error,
                cache_status = CASE WHEN episode.fingerprint != excluded.fingerprint THEN 'stale' ELSE episode.cache_status END,
                updated_at = excluded.updated_at
            """,
            (
                episode_id,
                source_id,
                relative_path,
                mcap_path.parent.name,
                data_group,
                str(mcap_path),
                str(summary_path) if summary_path else None,
                str(config_path) if config_path else None,
                fingerprint,
                stat.st_size,
                stat.st_mtime_ns,
                start_ns,
                end_ns,
                duration_ns,
                import_status,
                error,
                cache_status,
                now,
                now,
            ),
    )
    connection.execute("DELETE FROM stream WHERE episode_id = ?", (episode_id,))
    stream_keys = (
        "id", "episode_id", "topic", "stream_key", "stream_type", "display_name",
        "encoding", "schema_name", "adapter_id", "message_count", "first_time_ns",
        "last_time_ns", "nominal_hz", "available", "metadata_json",
    )
    connection.executemany(
        """
        INSERT INTO stream(
            id, episode_id, topic, stream_key, stream_type, display_name,
            encoding, schema_name, adapter_id, message_count, first_time_ns,
            last_time_ns, nominal_hz, available, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [tuple(stream[key] for key in stream_keys) for stream in streams],
    )

    camera_count = sum(1 for item in streams if item["stream_type"] == "camera" and item["available"])
    mocap_available = any(item["stream_type"] == "mocap" and item["available"] for item in streams)
    return {
        "id": episode_id,
        "episode_name": mcap_path.parent.name,
        "relative_path": relative_path,
        "mcap_path": str(mcap_path),
        "duration_ns": duration_ns,
        "camera_count": camera_count,
        "mocap_available": mocap_available,
        "import_status": import_status,
        "import_error": error,
        "unchanged": False,
    }


def _existing_episode_index_result(
    connection: sqlite3.Connection,
    episode: sqlite3.Row,
    *,
    unchanged: bool,
) -> dict[str, object]:
    stream_counts = connection.execute(
        """
        SELECT
            SUM(CASE WHEN stream_type = 'camera' AND available = 1 THEN 1 ELSE 0 END) AS camera_count,
            MAX(CASE WHEN stream_type = 'mocap' AND available = 1 THEN 1 ELSE 0 END) AS mocap_available
        FROM stream
        WHERE episode_id = ?
        """,
        (episode["id"],),
    ).fetchone()
    return {
        "id": episode["id"],
        "episode_name": episode["episode_name"],
        "relative_path": episode["relative_path"],
        "mcap_path": episode["mcap_path"],
        "duration_ns": episode["duration_ns"],
        "camera_count": int(stream_counts["camera_count"] or 0),
        "mocap_available": bool(stream_counts["mocap_available"]),
        "import_status": episode["import_status"],
        "import_error": episode["import_error"],
        "unchanged": unchanged,
    }


def _first_existing(directory: Path, names: Iterable[str]) -> Path | None:
    for name in names:
        path = directory / name
        if path.is_file():
            return path.resolve()
    return None


def _stream_from_channel(
    episode_id: str,
    topic: str,
    message_encoding: str,
    schema_name: str,
    message_count: int,
    start_ns: int | None,
    end_ns: int | None,
    duration_ns: int | None,
    profile: dict[str, object],
) -> dict[str, object]:
    stream_type, stream_key, display_name, adapter_id, encoding = _classify_stream(
        topic, message_encoding, schema_name, profile
    )
    nominal_hz = (message_count / (duration_ns / 1e9)) if message_count and duration_ns else None
    return {
        "id": _stable_id("str", episode_id, topic),
        "episode_id": episode_id,
        "topic": topic,
        "stream_key": stream_key,
        "stream_type": stream_type,
        "display_name": display_name,
        "encoding": encoding,
        "schema_name": schema_name or None,
        "adapter_id": adapter_id,
        "message_count": message_count,
        "first_time_ns": start_ns if message_count else None,
        "last_time_ns": end_ns if message_count else None,
        "nominal_hz": nominal_hz,
        "available": 1 if message_count > 0 else 0,
        "metadata_json": _json({"mcap_message_encoding": message_encoding}),
    }


def _classify_stream(
    topic: str,
    message_encoding: str,
    schema_name: str,
    profile: dict[str, object],
) -> tuple[str, str | None, str, str | None, str]:
    for definition in profile.get("streams", []) if isinstance(profile.get("streams"), list) else []:
        if not isinstance(definition, dict):
            continue
        patterns = definition.get("topic_patterns") or []
        if any(fnmatch(topic, str(pattern)) for pattern in patterns):
            stream_type = str(definition.get("type") or "stream")
            key = str(definition.get("key") or stream_type)
            display = str(definition.get("display_name") or (_camera_name(topic) if stream_type == "camera" else key))
            return stream_type, key, display, definition.get("adapter"), str(definition.get("encoding") or message_encoding)
    if schema_name == "foxglove.CompressedImage" or ("camera" in topic and ("image" in topic or topic.endswith("jpeg"))):
        return "camera", "camera", _camera_name(topic), "foxglove_compressed_image_v1", "jpeg"
    if topic == "/mocap/human_motion" or "mocap" in topic:
        return "mocap", "human_motion", "人体 Mocap", "human_motion_json_v1" if message_encoding == "json" else None, message_encoding
    if "retarget" in topic:
        return "retarget", "retarget", "重定向", None, message_encoding
    if "hand" in topic or "glove" in topic or "inspire" in topic:
        return "hand", "hand", topic.strip("/"), None, message_encoding
    if "robot" in topic:
        return "robot", "robot", topic.strip("/"), None, message_encoding
    return "stream", None, topic.strip("/") or topic, None, message_encoding


def _camera_name(topic: str) -> str:
    parts = [part for part in topic.split("/") if part]
    if "camera" in parts:
        index = parts.index("camera")
        middle = parts[index + 1 :]
        while middle and middle[-1] in {"jpeg", "raw", "image"}:
            middle.pop()
        if middle:
            return "/".join(middle)
    return topic.strip("/")


def _episode_rows(connection: sqlite3.Connection, where: str = "", parameters: tuple[object, ...] = ()) -> list[dict[str, object]]:
    query = f"""
        SELECT e.*, ds.root_path AS source_root, ds.task_id,
               t.task_code, t.task_name, t.origin AS task_origin,
               t.source_type,
               SUM(CASE WHEN s.stream_type = 'camera' AND s.available = 1 THEN 1 ELSE 0 END) AS camera_count,
               MAX(CASE WHEN s.stream_type = 'mocap' AND s.available = 1 THEN 1 ELSE 0 END) AS mocap_available
        FROM episode e
        JOIN data_source ds ON ds.id = e.data_source_id
        JOIN qc_task t ON t.id = ds.task_id
        LEFT JOIN stream s ON s.episode_id = e.id
        {where}
        GROUP BY e.id
        ORDER BY e.data_group COLLATE NOCASE, e.relative_path COLLATE NOCASE
    """
    rows = []
    for row in connection.execute(query, parameters):
        value = dict(row)
        value["camera_count"] = int(value["camera_count"] or 0)
        value["mocap_available"] = bool(value["mocap_available"])
        value["duration_sec"] = (value["duration_ns"] or 0) / 1e9
        rows.append(value)
    return rows


def _task_rows(
    connection: sqlite3.Connection,
    where: str = "",
    parameters: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    query = f"""
        SELECT t.*, ds.id AS data_source_id, ds.root_path, ds.last_scanned_at,
               COUNT(e.id) AS episode_count,
               SUM(CASE WHEN e.import_status = 'ready' THEN 1 ELSE 0 END) AS ready_count,
               SUM(CASE WHEN e.import_status != 'ready' THEN 1 ELSE 0 END) AS error_count,
               SUM(CASE WHEN e.review_status IN ('completed', 'reviewed') THEN 1 ELSE 0 END) AS completed_count,
               SUM(CASE WHEN e.review_status IN ('in_progress', 'needs_recheck') THEN 1 ELSE 0 END) AS active_count,
               COALESCE(SUM(e.file_size), 0) AS source_size_bytes
        FROM qc_task t
        LEFT JOIN data_source ds ON ds.task_id = t.id
        LEFT JOIN episode e ON e.data_source_id = ds.id
        {where}
        GROUP BY t.id
        ORDER BY t.updated_at DESC, t.created_at DESC
    """
    for row in connection.execute(query, parameters):
        value = dict(row)
        value["metadata"] = _loads(value.pop("metadata_json"), {})
        for key in (
            "episode_count",
            "ready_count",
            "error_count",
            "completed_count",
            "active_count",
            "source_size_bytes",
        ):
            value[key] = int(value[key] or 0)
        rows.append(value)
    return rows


def _task_row(connection: sqlite3.Connection, task_id: str) -> dict[str, object]:
    rows = _task_rows(connection, "WHERE t.id = ?", (task_id,))
    if not rows:
        raise KeyError(f"QC 任务不存在: {task_id}")
    return rows[0]


def _refresh_task_status(connection: sqlite3.Connection, task_id: str) -> None:
    task = connection.execute("SELECT status FROM qc_task WHERE id = ?", (task_id,)).fetchone()
    if task is None or task["status"] in {"submitted", "archived"}:
        return
    counts = connection.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN import_status = 'ready' THEN 1 ELSE 0 END) AS ready,
               SUM(CASE WHEN import_status != 'ready' THEN 1 ELSE 0 END) AS errors,
               SUM(CASE WHEN review_status IN ('completed', 'reviewed') THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN review_status IN ('in_progress', 'needs_recheck') THEN 1 ELSE 0 END) AS active
        FROM episode e
        JOIN data_source ds ON ds.id = e.data_source_id
        WHERE ds.task_id = ?
        """,
        (task_id,),
    ).fetchone()
    total = int(counts["total"] or 0)
    ready = int(counts["ready"] or 0)
    errors = int(counts["errors"] or 0)
    completed = int(counts["completed"] or 0)
    active = int(counts["active"] or 0)
    if total == 0 or ready == 0:
        status = "failed"
    elif completed == total and errors == 0:
        status = "completed"
    elif active or completed:
        status = "in_progress"
    else:
        status = "ready"
    connection.execute(
        "UPDATE qc_task SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), task_id),
    )


def list_qc_tasks(db_path: str | Path) -> list[dict[str, object]]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        return _task_rows(connection)


def get_qc_task(db_path: str | Path, task_id: str) -> dict[str, object]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        return _task_row(connection, task_id)


def mark_qc_task_submitted(db_path: str | Path, flow_job_code: str) -> dict[str, object]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        task = connection.execute(
            "SELECT id FROM qc_task WHERE flow_job_code = ?",
            (flow_job_code,),
        ).fetchone()
        if task is None:
            raise KeyError(f"本地不存在 Flow 质检任务: {flow_job_code}")
        connection.execute(
            "UPDATE qc_task SET status = 'submitted', updated_at = ? WHERE id = ?",
            (_now(), task["id"]),
        )
        return _task_row(connection, str(task["id"]))


def clear_local_task_history(
    db_path: str | Path, *, keep_task_id: str | None = None
) -> dict[str, object]:
    """Remove non-Flow task indexes while leaving every source directory untouched."""

    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        where = "WHERE t.origin != 'flow'"
        parameters: tuple[object, ...] = ()
        if keep_task_id:
            where += " AND t.id != ?"
            parameters = (keep_task_id,)
        tasks = _task_rows(connection, where, parameters)
        removed_episode_ids: list[str] = []
        for task in tasks:
            episode_rows = connection.execute(
                """
                SELECT e.id FROM episode e
                JOIN data_source ds ON ds.id = e.data_source_id
                WHERE ds.task_id = ?
                """,
                (task["id"],),
            ).fetchall()
            episode_ids = [str(row["id"]) for row in episode_rows]
            removed_episode_ids.extend(episode_ids)
            if episode_ids:
                placeholders = ",".join("?" for _ in episode_ids)
                annotation_rows = connection.execute(
                    f"SELECT id FROM annotation WHERE episode_id IN ({placeholders})",
                    tuple(episode_ids),
                ).fetchall()
                annotation_ids = [str(row["id"]) for row in annotation_rows]
                if annotation_ids:
                    annotation_placeholders = ",".join("?" for _ in annotation_ids)
                    connection.execute(
                        f"DELETE FROM change_log WHERE entity_type = 'annotation' "
                        f"AND entity_id IN ({annotation_placeholders})",
                        tuple(annotation_ids),
                    )
                connection.execute(
                    f"DELETE FROM annotation WHERE episode_id IN ({placeholders})",
                    tuple(episode_ids),
                )
                connection.execute(
                    f"DELETE FROM episode WHERE id IN ({placeholders})",
                    tuple(episode_ids),
                )
            connection.execute("DELETE FROM data_source WHERE task_id = ?", (task["id"],))
            connection.execute("DELETE FROM qc_task WHERE id = ?", (task["id"],))
        return {
            "removed_count": len(tasks),
            "removed_tasks": tasks,
            "removed_episode_ids": removed_episode_ids,
            "source_files_deleted": False,
        }


def list_label_sets(db_path: str | Path) -> list[dict[str, object]]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        workspace = connection.execute(
            "SELECT active_label_set_id FROM workspace LIMIT 1"
        ).fetchone()
        active_id = str(workspace["active_label_set_id"] or "")
        rows = connection.execute(
            """
            SELECT ls.*,
                   COUNT(ld.id) AS label_count,
                   (
                       SELECT COUNT(*) FROM annotation a
                       WHERE a.label_set_key = ls.label_set_key
                         AND a.label_schema_version = ls.version
                         AND a.deleted_at IS NULL
                   ) AS annotation_count
            FROM label_set ls
            LEFT JOIN label_definition ld ON ld.label_set_id = ls.id AND ld.enabled = 1
            WHERE ls.enabled = 1
            GROUP BY ls.id
            ORDER BY ls.created_at DESC, ls.name COLLATE NOCASE
            """
        ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "label_set_id": str(row["label_set_key"]),
                "name": str(row["name"]),
                "version": str(row["version"]),
                "language": str(row["language"]),
                "source_format": str(row["source_format"]),
                "label_count": int(row["label_count"] or 0),
                "annotation_count": int(row["annotation_count"] or 0),
                "active": str(row["id"]) == active_id,
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]


def activate_label_set(db_path: str | Path, label_set_id: str) -> dict[str, object]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        label_set = connection.execute(
            "SELECT id FROM label_set WHERE id = ? AND enabled = 1",
            (label_set_id,),
        ).fetchone()
        if label_set is None:
            raise KeyError(f"标签库不存在: {label_set_id}")
        connection.execute(
            "UPDATE workspace SET active_label_set_id = ?, updated_at = ?",
            (label_set_id, _now()),
        )
    return next(item for item in list_label_sets(db_path) if item["id"] == label_set_id)


def delete_label_set(db_path: str | Path, label_set_id: str) -> dict[str, object]:
    """Hide a label set while retaining definitions used by historical annotations."""

    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        workspace = connection.execute(
            "SELECT active_label_set_id FROM workspace LIMIT 1"
        ).fetchone()
        label_set = connection.execute(
            "SELECT id, name FROM label_set WHERE id = ? AND enabled = 1",
            (label_set_id,),
        ).fetchone()
        if label_set is None:
            raise KeyError(f"标签库不存在: {label_set_id}")
        enabled_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM label_set WHERE enabled = 1"
            ).fetchone()[0]
        )
        if enabled_count <= 1:
            raise ValueError("至少保留一个可用标签库")
        replacement_id = None
        if str(workspace["active_label_set_id"] or "") == label_set_id:
            replacement = connection.execute(
                """
                SELECT id FROM label_set
                WHERE enabled = 1 AND id != ?
                ORDER BY created_at DESC, name COLLATE NOCASE
                LIMIT 1
                """,
                (label_set_id,),
            ).fetchone()
            replacement_id = str(replacement["id"])
            connection.execute(
                "UPDATE workspace SET active_label_set_id = ?, updated_at = ?",
                (replacement_id, _now()),
            )
        connection.execute(
            "UPDATE label_set SET enabled = 0 WHERE id = ?", (label_set_id,)
        )
        deleted = {"id": label_set_id, "name": str(label_set["name"])}
    return {
        "deleted": deleted,
        "replacement_id": replacement_id,
        "label_sets": list_label_sets(db_path),
    }


def rescan_qc_task(
    db_path: str | Path,
    task_id: str,
    *,
    profile_path: str | Path | None = None,
) -> dict[str, object]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        task = _task_row(connection, task_id)
    return scan_data_source(
        db_path,
        str(task["local_source_path"]),
        profile_path=profile_path,
        task_code=str(task["task_code"]),
        task_name=str(task["task_name"]),
        origin=str(task["origin"]),
        flow_job_code=str(task["flow_job_code"]) if task.get("flow_job_code") else None,
        asset_id=str(task["asset_id"]) if task.get("asset_id") else None,
        source_uri=str(task["source_uri"]),
        task_metadata=task.get("metadata") if isinstance(task.get("metadata"), dict) else None,
    )


def workspace_state(db_path: str | Path, *, task_id: str | None = None) -> dict[str, object]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        workspace = dict(connection.execute("SELECT * FROM workspace LIMIT 1").fetchone())
        workspace["settings"] = _loads(workspace.pop("settings_json"), {})
        sources = [dict(row) for row in connection.execute("SELECT * FROM data_source ORDER BY created_at")]
        tasks = _task_rows(connection)
        selected_task = None
        if task_id:
            selected_task = _task_row(connection, task_id)
            episodes = _episode_rows(connection, "WHERE ds.task_id = ?", (task_id,))
        else:
            episodes = _episode_rows(connection)
        label_schema = _active_label_schema(connection)
    return {
        "workspace": workspace,
        "sources": sources,
        "tasks": tasks,
        "selected_task": selected_task,
        "episodes": episodes,
        "label_schema": label_schema,
    }


def episode_detail(db_path: str | Path, episode_id: str) -> dict[str, object]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        rows = _episode_rows(connection, "WHERE e.id = ?", (episode_id,))
        if not rows:
            raise KeyError(f"Episode 不存在: {episode_id}")
        episode = rows[0]
        streams = []
        for row in connection.execute("SELECT * FROM stream WHERE episode_id = ? ORDER BY stream_type, topic", (episode_id,)):
            value = dict(row)
            value["available"] = bool(value["available"])
            value["metadata"] = _loads(value.pop("metadata_json"), {})
            streams.append(value)
        annotations = _list_annotations(connection, episode_id)
        schema = _active_label_schema(connection)
    return {"episode": episode, "streams": streams, "annotations": annotations, "label_schema": schema}


def parse_label_schema(path: str | Path) -> tuple[dict[str, object], str, str, str]:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    raw_bytes = source_path.read_bytes()
    if suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(raw_bytes.decode("utf-8"))
        source_format = "yaml"
    elif suffix == ".json":
        payload = json.loads(raw_bytes.decode("utf-8"))
        source_format = "json"
    elif suffix == ".csv":
        payload = _parse_label_csv(raw_bytes.decode("utf-8-sig"), source_path.stem)
        source_format = "csv"
    else:
        raise ValueError("标签库只支持 YAML、JSON 或 CSV")
    if not isinstance(payload, dict):
        raise ValueError("标签库顶层必须是对象")
    template_mode = "full" if isinstance(payload.get("schema"), dict) else "simple"
    normalized = _normalize_label_schema(payload, fallback_name=source_path.stem)
    return normalized, source_format, hashlib.sha256(raw_bytes).hexdigest(), template_mode


def _parse_label_csv(text: str, name: str) -> dict[str, object]:
    rows = list(csv.DictReader(text.splitlines()))
    fieldnames = set(rows[0]) if rows else set()
    if fieldnames & {"标签名称", "名称"}:
        labels = []
        for row in rows:
            labels.append(
                {
                    "编码": (row.get("编码") or row.get("标签编码") or row.get("code") or "").strip(),
                    "名称": (row.get("标签名称") or row.get("名称") or "").strip(),
                    "分组": (row.get("分组") or "").strip(),
                    "说明": (row.get("说明") or row.get("判断标准") or "").strip(),
                    "范围": (row.get("范围") or row.get("标注范围") or "").strip(),
                    "对象": (row.get("对象") or row.get("标注对象") or "").strip(),
                    "严重程度": (row.get("严重程度") or "").strip(),
                    "处理建议": (row.get("处理建议") or "").strip(),
                    "快捷键": (row.get("快捷键") or "").strip(),
                    "颜色": (row.get("颜色") or "").strip(),
                }
            )
        return {"标签库名称": name, "版本": "1.0.0", "标签": labels}
    groups: dict[str, dict[str, object]] = {}
    labels: list[dict[str, object]] = []
    for row in rows:
        group = (row.get("group") or "custom").strip()
        groups.setdefault(group, {"code": group, "name": group, "order": len(groups) + 1})
        labels.append(
            {
                "code": (row.get("code") or "").strip(),
                "name": (row.get("name") or "").strip(),
                "group": group,
                "description": (row.get("description") or "").strip(),
                "enabled": (row.get("enabled") or "true").strip().lower() not in {"0", "false", "no"},
                "annotation_scopes": _split_csv_multi(row.get("annotation_scopes")),
                "target_types": _split_csv_multi(row.get("target_types")),
                "default_severity": (row.get("default_severity") or "").strip() or None,
                "default_action": (row.get("default_action") or "").strip() or None,
                "shortcut": (row.get("shortcut") or "").strip() or None,
                "color": (row.get("color") or "").strip() or None,
                "applicable_profiles": _split_csv_multi(row.get("applicable_profiles")),
            }
        )
    return {
        "schema": {
            "schema_type": "annotation_label_schema",
            "schema_version": "1.0.0",
            "label_set_id": re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "imported_labels",
            "label_set_name": name,
            "language": "zh-CN",
        },
        "severity_levels": DEFAULT_SEVERITY_LEVELS,
        "actions": DEFAULT_LABEL_ACTIONS,
        "groups": list(groups.values()),
        "labels": labels,
    }


def _split_csv_multi(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def _normalize_label_schema(
    payload: dict[str, object], *, fallback_name: str = "自定义标签库"
) -> dict[str, object]:
    if not isinstance(payload.get("schema"), dict):
        return _normalize_simple_label_schema(payload, fallback_name=fallback_name)
    result = json.loads(json.dumps(payload, ensure_ascii=False))
    result.setdefault("severity_levels", [])
    result.setdefault("actions", [])
    result.setdefault("groups", [])
    result.setdefault("labels", [])
    for label in result["labels"]:
        label.setdefault("enabled", True)
        label.setdefault("description", "")
        label.setdefault("fields", [])
        label.setdefault("applicable_profiles", [])
    return result


def _normalize_simple_label_schema(
    payload: dict[str, object], *, fallback_name: str
) -> dict[str, object]:
    raw_labels = payload.get("标签", payload.get("labels"))
    if not isinstance(raw_labels, list):
        raise ValueError("简易标签模板缺少“标签”列表；请下载模板后在“标签:”下面填写")
    label_set_name = str(
        payload.get("标签库名称") or payload.get("名称") or payload.get("name") or fallback_name
    ).strip()
    if not label_set_name:
        raise ValueError("请填写“标签库名称”")
    version = str(payload.get("版本") or payload.get("version") or "1.0.0").strip()
    explicit_set_code = payload.get("标签库编码") or payload.get("code")
    label_set_id = _simple_code(explicit_set_code or label_set_name, "labels")

    groups: dict[str, dict[str, object]] = {}
    labels: list[dict[str, object]] = []
    for index, raw_label in enumerate(raw_labels, start=1):
        if isinstance(raw_label, str):
            item: dict[str, object] = {"名称": raw_label}
        elif isinstance(raw_label, dict):
            item = raw_label
        else:
            raise ValueError(f"第 {index} 个标签必须写成名称或字段列表")
        name = str(
            item.get("名称") or item.get("标签名称") or item.get("name") or ""
        ).strip()
        if not name:
            raise ValueError(f"第 {index} 个标签没有填写“名称”")
        code = str(item.get("编码") or item.get("标签编码") or item.get("code") or "").strip()
        if not code:
            raise ValueError(
                f"标签“{name}”没有填写“编码”；请填写可读的英文编码，例如 clothes_drop"
            )
        if not LABEL_CODE_RE.fullmatch(code):
            raise ValueError(
                f"标签“{name}”的编码“{code}”格式不正确；编码必须以小写字母开头，"
                "只能包含小写字母、数字和下划线，长度为 3～64 个字符"
            )
        group_name = str(item.get("分组") or item.get("group") or "其他").strip() or "其他"
        group_code = _simple_code(group_name, "group")
        if group_code not in groups:
            groups[group_code] = {
                "code": group_code,
                "name": group_name,
                "order": len(groups) + 1,
            }
        scopes = _simple_multi_values(
            item.get("范围", item.get("标注范围", item.get("annotation_scopes"))),
            aliases=SIMPLE_SCOPE_ALIASES,
            default=("time_range", "time_point", "episode"),
            field_name="范围",
            label_name=name,
            examples="区间、时间点、整条、全部",
        )
        targets = _simple_multi_values(
            item.get("对象", item.get("标注对象", item.get("target_types"))),
            aliases=SIMPLE_TARGET_ALIASES,
            default=("global",),
            field_name="对象",
            label_name=name,
            examples="全局、画面、动捕、关节、全部",
        )
        severity = _simple_single_value(
            item.get("严重程度", item.get("default_severity")),
            aliases=SIMPLE_SEVERITY_ALIASES,
            default="normal",
            field_name="严重程度",
            label_name=name,
            examples="轻微、一般、严重",
        )
        action = _simple_single_value(
            item.get("处理建议", item.get("default_action")),
            aliases=SIMPLE_ACTION_ALIASES,
            default="keep_with_label",
            field_name="处理建议",
            label_name=name,
            examples="保留、保留但标记、裁剪、修复、重采、废弃、待复核",
        )
        color = str(item.get("颜色") or item.get("color") or "").strip()
        if not color:
            color = SIMPLE_GROUP_COLORS[(groups[group_code]["order"] - 1) % len(SIMPLE_GROUP_COLORS)]
        labels.append(
            {
                "code": code,
                "name": name,
                "group": group_code,
                "description": str(
                    item.get("说明") or item.get("判断标准") or item.get("description") or ""
                ).strip(),
                "enabled": _simple_enabled(item.get("启用", item.get("enabled", True))),
                "annotation_scopes": scopes,
                "target_types": targets,
                "default_severity": severity,
                "default_action": action,
                "shortcut": str(item.get("快捷键") or item.get("shortcut") or "").strip().upper() or None,
                "color": color,
                "applicable_profiles": [],
                "fields": item.get("fields") if isinstance(item.get("fields"), list) else [],
            }
        )
    return {
        "schema": {
            "schema_type": "annotation_label_schema",
            "schema_version": version,
            "label_set_id": label_set_id,
            "label_set_name": label_set_name,
            "language": "zh-CN",
        },
        "severity_levels": json.loads(json.dumps(DEFAULT_SEVERITY_LEVELS)),
        "actions": json.loads(json.dumps(DEFAULT_LABEL_ACTIONS)),
        "groups": list(groups.values()),
        "labels": labels,
    }


def _simple_code(value: object, prefix: str) -> str:
    text = str(value or "").strip()
    ascii_code = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    if ascii_code and ascii_code[0].isalpha():
        candidate = ascii_code[:63]
        if len(candidate) >= 3:
            return candidate
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _simple_tokens(value: object) -> list[str]:
    if value is None or value == "":
        return []
    values = value if isinstance(value, list) else [value]
    tokens: list[str] = []
    for item in values:
        tokens.extend(
            part.strip().lower()
            for part in re.split(r"[,，、|/；;]+|\s*(?:或|和)\s*", str(item))
            if part.strip()
        )
    return tokens


def _simple_multi_values(
    value: object,
    *,
    aliases: dict[str, tuple[str, ...]],
    default: tuple[str, ...],
    field_name: str,
    label_name: str,
    examples: str,
) -> list[str]:
    tokens = _simple_tokens(value)
    if not tokens:
        return list(default)
    result: list[str] = []
    for token in tokens:
        mapped = aliases.get(token)
        if mapped is None:
            raise ValueError(
                f"标签“{label_name}”的{field_name}“{token}”无法识别；可填写：{examples}"
            )
        for item in mapped:
            if item not in result:
                result.append(item)
    return result


def _simple_single_value(
    value: object,
    *,
    aliases: dict[str, str],
    default: str,
    field_name: str,
    label_name: str,
    examples: str,
) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return default
    result = aliases.get(text)
    if result is None:
        raise ValueError(
            f"标签“{label_name}”的{field_name}“{text}”无法识别；可填写：{examples}"
        )
    return result


def _simple_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "否", "停用"}


def validate_label_schema(schema: dict[str, object]) -> list[str]:
    errors: list[str] = []
    header = schema.get("schema")
    if not isinstance(header, dict):
        return ["缺少 schema 对象"]
    if header.get("schema_type") != "annotation_label_schema":
        errors.append("schema.schema_type 必须为 annotation_label_schema")
    for key in ("schema_version", "label_set_id", "label_set_name"):
        if not header.get(key):
            errors.append(f"schema.{key} 不能为空")
    if not schema.get("labels"):
        errors.append("标签库至少需要一个标签")

    groups = {str(item.get("code")) for item in schema.get("groups", []) if isinstance(item, dict)}
    severities = {str(item.get("code")) for item in schema.get("severity_levels", []) if isinstance(item, dict)}
    actions = {str(item.get("code")) for item in schema.get("actions", []) if isinstance(item, dict)}
    codes: set[str] = set()
    shortcuts: set[str] = set()
    for index, label in enumerate(schema.get("labels", [])):
        if not isinstance(label, dict):
            errors.append(f"labels[{index}] 必须是对象")
            continue
        prefix = f"labels[{index}]"
        code = str(label.get("code") or "")
        if not LABEL_CODE_RE.fullmatch(code):
            errors.append(f"{prefix}.code 无效: {code}")
        elif code in codes:
            errors.append(f"标签编码重复: {code}")
        codes.add(code)
        if not label.get("name"):
            errors.append(f"{prefix}.name 不能为空")
        if str(label.get("group")) not in groups:
            errors.append(f"{prefix}.group 不存在: {label.get('group')}")
        scopes = set(label.get("annotation_scopes") or [])
        targets = set(label.get("target_types") or [])
        if not scopes or not scopes <= VALID_SCOPES:
            errors.append(f"{prefix}.annotation_scopes 含有无效值")
        if not targets or not targets <= VALID_TARGETS:
            errors.append(f"{prefix}.target_types 含有无效值")
        severity = label.get("default_severity")
        action = label.get("default_action")
        if severity and str(severity) not in severities:
            errors.append(f"{prefix}.default_severity 不存在: {severity}")
        if action and str(action) not in actions:
            errors.append(f"{prefix}.default_action 不存在: {action}")
        color = label.get("color")
        if color and not COLOR_RE.fullmatch(str(color)):
            errors.append(f"{prefix}.color 不是 #RRGGBB")
        shortcut = str(label.get("shortcut") or "").upper()
        if shortcut:
            if shortcut in RESERVED_SHORTCUTS:
                errors.append(f"{prefix}.shortcut 占用系统快捷键: {shortcut}")
            elif shortcut in shortcuts:
                errors.append(f"标签快捷键重复: {shortcut}")
            shortcuts.add(shortcut)
        field_codes: set[str] = set()
        for field in label.get("fields") or []:
            field_code = str(field.get("code") or "") if isinstance(field, dict) else ""
            if not field_code or field_code in field_codes:
                errors.append(f"{prefix}.fields 编码为空或重复: {field_code}")
            field_codes.add(field_code)
            if isinstance(field, dict) and field.get("type") in {"select", "multi_select"} and not field.get("options"):
                errors.append(f"{prefix}.fields.{field_code} 缺少 options")
    return errors


def preview_label_schema(db_path: str | Path, schema_path: str | Path) -> dict[str, object]:
    initialize_workspace(db_path)
    schema, source_format, source_hash, template_mode = parse_label_schema(schema_path)
    errors = validate_label_schema(schema)
    header = schema.get("schema", {})
    incoming = {item["code"]: item for item in schema.get("labels", []) if isinstance(item, dict) and item.get("code")}
    existing: dict[str, dict[str, object]] = {}
    with connect_workspace(db_path) as connection:
        active = _active_label_schema(connection)
        if active and active.get("schema", {}).get("label_set_id") == header.get("label_set_id"):
            existing = {item["code"]: item for item in active.get("labels", [])}
    added = sorted(set(incoming) - set(existing))
    updated = sorted(code for code in set(incoming) & set(existing) if incoming[code] != existing[code])
    unchanged = sorted(code for code in set(incoming) & set(existing) if incoming[code] == existing[code])
    return {
        "valid": not errors,
        "errors": errors,
        "source_format": source_format,
        "source_hash": source_hash,
        "template_mode": template_mode,
        "label_set_id": header.get("label_set_id"),
        "version": header.get("schema_version"),
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "preserved": sorted(set(existing) - set(incoming)),
        "schema": schema,
    }


def import_label_schema(db_path: str | Path, schema_path: str | Path) -> dict[str, object]:
    preview = preview_label_schema(db_path, schema_path)
    if not preview["valid"]:
        raise ValueError("标签库校验失败: " + "; ".join(preview["errors"]))
    schema = preview["schema"]
    _activate_label_schema(
        db_path,
        schema,
        source_format=str(preview["source_format"]),
        source_hash=str(preview["source_hash"]),
    )
    return {key: value for key, value in preview.items() if key != "schema"} | {"active": True}


def import_flow_label_schema(db_path: str | Path, job: dict) -> dict[str, object] | None:
    """Install the immutable task schema delivered with a Flow QC job."""

    initialize_workspace(db_path)
    reference_fields = (
        "label_set_id",
        "label_schema_version",
        "label_schema_hash",
    )
    supplied = {field: str(job.get(field) or "").strip() for field in reference_fields}
    if not any(supplied.values()):
        return None
    missing = [field for field, value in supplied.items() if not value]
    if missing:
        raise ValueError("Flow 质检任务的标签库引用不完整：" + "、".join(missing))
    raw_schema = job.get("label_schema")
    if not isinstance(raw_schema, dict):
        raise ValueError("Flow 质检任务缺少冻结标签库快照")
    schema_hash = canonical_json_sha256(raw_schema)
    if schema_hash.lower() != supplied["label_schema_hash"].lower():
        raise ValueError("Flow 任务标签库快照摘要与冻结版本引用不一致")
    schema = _normalize_label_schema(raw_schema, fallback_name=supplied["label_set_id"])
    errors = validate_label_schema(schema)
    if errors:
        raise ValueError("Flow 任务标签库校验失败: " + "; ".join(errors))
    header = schema["schema"]
    if (
        str(header["label_set_id"]) != supplied["label_set_id"]
        or str(header["schema_version"]) != supplied["label_schema_version"]
    ):
        raise ValueError("Flow 任务标签库快照与冻结版本引用不一致")
    with connect_workspace(db_path) as connection:
        existing = connection.execute(
            "SELECT source_hash FROM label_set WHERE label_set_key = ? AND version = ?",
            (supplied["label_set_id"], supplied["label_schema_version"]),
        ).fetchone()
    if existing and str(existing["source_hash"]).lower() != supplied["label_schema_hash"].lower():
        raise ValueError("本地已有同版本但摘要不同的标签库，必须使用新的 schema_version")
    _activate_label_schema(
        db_path,
        schema,
        source_format="flow",
        source_hash=supplied["label_schema_hash"],
    )
    return {
        "label_set_id": supplied["label_set_id"],
        "version": supplied["label_schema_version"],
        "source_hash": supplied["label_schema_hash"],
        "active": True,
    }


def _activate_label_schema(
    db_path: str | Path,
    schema: dict[str, object],
    *,
    source_format: str,
    source_hash: str,
) -> None:
    header = schema["schema"]
    label_set_id = _stable_id("ls", header["label_set_id"], header["schema_version"])
    with connect_workspace(db_path) as connection:
        connection.execute(
            """
            INSERT INTO label_set(id, label_set_key, name, version, language, source_format, source_hash, enabled, raw_schema_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(label_set_key, version) DO UPDATE SET
                name = excluded.name, language = excluded.language, source_format = excluded.source_format,
                source_hash = excluded.source_hash, raw_schema_json = excluded.raw_schema_json, enabled = 1
            """,
            (
                label_set_id,
                header["label_set_id"],
                header["label_set_name"],
                header["schema_version"],
                header.get("language", "zh-CN"),
                source_format,
                source_hash,
                _json(schema),
                _now(),
            ),
        )
        actual = connection.execute(
            "SELECT id FROM label_set WHERE label_set_key = ? AND version = ?",
            (header["label_set_id"], header["schema_version"]),
        ).fetchone()["id"]
        connection.execute("DELETE FROM label_definition WHERE label_set_id = ?", (actual,))
        for label in schema["labels"]:
            connection.execute(
                """
                INSERT INTO label_definition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _stable_id("lbl", actual, label["code"]),
                    actual,
                    label["code"],
                    label["name"],
                    label["group"],
                    label.get("description"),
                    1 if label.get("enabled", True) else 0,
                    _json(label["annotation_scopes"]),
                    _json(label["target_types"]),
                    label.get("default_severity"),
                    label.get("default_action"),
                    label.get("shortcut"),
                    label.get("color"),
                    _json(label.get("applicable_profiles", [])),
                    _json(label.get("fields", [])),
                ),
            )
        connection.execute("UPDATE workspace SET active_label_set_id = ?, updated_at = ?", (actual, _now()))


def _active_label_schema(connection: sqlite3.Connection) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT ls.raw_schema_json FROM workspace w
        LEFT JOIN label_set ls ON ls.id = w.active_label_set_id
        LIMIT 1
        """
    ).fetchone()
    return _loads(row["raw_schema_json"], None) if row and row["raw_schema_json"] else None


def _annotation_row(row: sqlite3.Row) -> dict[str, object]:
    value = dict(row)
    value["annotation_id"] = value.pop("id")
    value["attributes"] = _loads(value.pop("attributes_json"), {})
    return value


def _list_annotations(connection: sqlite3.Connection, episode_id: str) -> list[dict[str, object]]:
    return [
        _annotation_row(row)
        for row in connection.execute(
            "SELECT * FROM annotation WHERE episode_id = ? AND deleted_at IS NULL ORDER BY start_offset_ns, created_at",
            (episode_id,),
        )
    ]


def list_annotations(db_path: str | Path, episode_id: str) -> list[dict[str, object]]:
    with connect_workspace(db_path) as connection:
        return _list_annotations(connection, episode_id)


def save_annotation(
    db_path: str | Path,
    payload: dict[str, object],
    *,
    annotation_id: str | None = None,
    session_id: str = "default",
    expected_updated_at: str | None = None,
) -> dict[str, object]:
    initialize_workspace(db_path)
    with connect_workspace(db_path) as connection:
        episode_id = str(payload.get("episode_id") or "")
        episode = connection.execute("SELECT * FROM episode WHERE id = ?", (episode_id,)).fetchone()
        if not episode:
            raise KeyError(f"Episode 不存在: {episode_id}")
        workspace = connection.execute("SELECT * FROM workspace LIMIT 1").fetchone()
        active_id = workspace["active_label_set_id"]
        if not active_id:
            raise ValueError("尚未导入并激活标签库")
        label_set = connection.execute("SELECT * FROM label_set WHERE id = ?", (active_id,)).fetchone()
        label = connection.execute(
            "SELECT * FROM label_definition WHERE label_set_id = ? AND code = ? AND enabled = 1",
            (active_id, payload.get("label_code")),
        ).fetchone()
        if not label:
            raise ValueError(f"标签不存在或已停用: {payload.get('label_code')}")
        normalized = _validate_annotation_payload(payload, episode, label)
        now = _now()
        old_row = connection.execute("SELECT * FROM annotation WHERE id = ?", (annotation_id,)).fetchone() if annotation_id else None
        if annotation_id and not old_row:
            raise KeyError(f"标注不存在: {annotation_id}")
        if old_row and expected_updated_at is not None and old_row["updated_at"] != expected_updated_at:
            raise WorkspaceConflictError("该标注已在另一个页面中更新，请刷新后重试")
        before = _annotation_row(old_row) if old_row else None
        actual_id = annotation_id or _new_id("ann")
        created_at = old_row["created_at"] if old_row else now
        connection.execute(
            """
            INSERT INTO annotation(
                id, episode_id, label_set_key, label_schema_version, label_code, scope,
                start_offset_ns, end_offset_ns, target_type, target_key, severity, action,
                comment, attributes_json, source, status, reviewer_name, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(id) DO UPDATE SET
                label_code = excluded.label_code, scope = excluded.scope,
                start_offset_ns = excluded.start_offset_ns, end_offset_ns = excluded.end_offset_ns,
                target_type = excluded.target_type, target_key = excluded.target_key,
                severity = excluded.severity, action = excluded.action, comment = excluded.comment,
                attributes_json = excluded.attributes_json, status = excluded.status,
                reviewer_name = excluded.reviewer_name, updated_at = excluded.updated_at, deleted_at = NULL
            """,
            (
                actual_id,
                episode_id,
                label_set["label_set_key"],
                label_set["version"],
                normalized["label_code"],
                normalized["scope"],
                normalized["start_offset_ns"],
                normalized["end_offset_ns"],
                normalized["target_type"],
                normalized.get("target_key"),
                normalized.get("severity") or label["default_severity"],
                normalized.get("action") or label["default_action"],
                normalized.get("comment", ""),
                _json(normalized.get("attributes", {})),
                "manual",
                normalized.get("status", "confirmed"),
                normalized.get("reviewer_name") or workspace["reviewer_name"],
                created_at,
                now,
            ),
        )
        _refresh_annotation_count(connection, episode_id)
        saved_row = connection.execute("SELECT * FROM annotation WHERE id = ?", (actual_id,)).fetchone()
        saved = _annotation_row(saved_row)
        _record_change(connection, actual_id, "update" if old_row else "create", before, saved, session_id)
        if episode["review_status"] == "unreviewed":
            connection.execute("UPDATE episode SET review_status = 'in_progress', updated_at = ? WHERE id = ?", (now, episode_id))
        return saved


def _validate_annotation_payload(payload: dict[str, object], episode: sqlite3.Row, label: sqlite3.Row) -> dict[str, object]:
    value = dict(payload)
    scope = str(value.get("scope") or "")
    target = str(value.get("target_type") or "")
    scopes = set(_loads(label["scopes_json"], []))
    targets = set(_loads(label["targets_json"], []))
    if scope not in VALID_SCOPES or scope not in scopes:
        raise ValueError(f"标签 {label['code']} 不支持范围 {scope}")
    if target not in VALID_TARGETS or target not in targets:
        raise ValueError(f"标签 {label['code']} 不支持目标 {target}")
    duration = int(episode["duration_ns"] or 0)
    start = int(value.get("start_offset_ns") or 0)
    end = int(value.get("end_offset_ns") or 0)
    if scope == "episode":
        start, end = 0, duration
    if start < 0 or end < start or end > duration:
        raise ValueError(f"标注时间越界: {start}..{end}, Episode 时长 {duration}")
    if scope == "time_range" and end <= start:
        raise ValueError("区间标签要求结束时间大于开始时间")
    if scope == "time_point" and end != start:
        raise ValueError("时间点标签要求开始与结束时间相同")
    attributes = value.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise ValueError("attributes 必须是对象")
    for field in _loads(label["fields_json"], []):
        if field.get("required") and not attributes.get(field.get("code")):
            raise ValueError(f"缺少必填字段: {field.get('name') or field.get('code')}")
    value.update({"scope": scope, "target_type": target, "start_offset_ns": start, "end_offset_ns": end, "attributes": attributes})
    return value


def delete_annotation(db_path: str | Path, annotation_id: str, *, session_id: str = "default") -> dict[str, object]:
    with connect_workspace(db_path) as connection:
        row = connection.execute("SELECT * FROM annotation WHERE id = ? AND deleted_at IS NULL", (annotation_id,)).fetchone()
        if not row:
            raise KeyError(f"标注不存在: {annotation_id}")
        before = _annotation_row(row)
        now = _now()
        connection.execute("UPDATE annotation SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, annotation_id))
        _refresh_annotation_count(connection, row["episode_id"])
        _record_change(connection, annotation_id, "delete", before, None, session_id)
        return before


def _record_change(
    connection: sqlite3.Connection,
    entity_id: str,
    operation: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    session_id: str,
) -> None:
    connection.execute("DELETE FROM change_log WHERE session_id = ? AND undone = 1", (session_id,))
    connection.execute(
        "INSERT INTO change_log(entity_type, entity_id, operation, before_json, after_json, session_id, undone, created_at) VALUES ('annotation', ?, ?, ?, ?, ?, 0, ?)",
        (entity_id, operation, _json(before) if before else None, _json(after) if after else None, session_id, _now()),
    )
    connection.execute(
        "DELETE FROM change_log WHERE id IN (SELECT id FROM change_log WHERE session_id = ? ORDER BY id DESC LIMIT -1 OFFSET 50)",
        (session_id,),
    )


def undo_annotation_change(db_path: str | Path, *, session_id: str = "default") -> dict[str, object] | None:
    with connect_workspace(db_path) as connection:
        change = connection.execute(
            "SELECT * FROM change_log WHERE session_id = ? AND undone = 0 ORDER BY id DESC LIMIT 1", (session_id,)
        ).fetchone()
        if not change:
            return None
        _restore_annotation_snapshot(connection, change["entity_id"], _loads(change["before_json"], None))
        connection.execute("UPDATE change_log SET undone = 1 WHERE id = ?", (change["id"],))
        return {"operation": "undo", "entity_id": change["entity_id"]}


def redo_annotation_change(db_path: str | Path, *, session_id: str = "default") -> dict[str, object] | None:
    with connect_workspace(db_path) as connection:
        change = connection.execute(
            "SELECT * FROM change_log WHERE session_id = ? AND undone = 1 ORDER BY id ASC LIMIT 1", (session_id,)
        ).fetchone()
        if not change:
            return None
        _restore_annotation_snapshot(connection, change["entity_id"], _loads(change["after_json"], None))
        connection.execute("UPDATE change_log SET undone = 0 WHERE id = ?", (change["id"],))
        return {"operation": "redo", "entity_id": change["entity_id"]}


def _restore_annotation_snapshot(connection: sqlite3.Connection, annotation_id: str, snapshot: dict[str, object] | None) -> None:
    row = connection.execute("SELECT episode_id FROM annotation WHERE id = ?", (annotation_id,)).fetchone()
    episode_id = row["episode_id"] if row else (snapshot or {}).get("episode_id")
    if snapshot is None:
        connection.execute("UPDATE annotation SET deleted_at = ?, updated_at = ? WHERE id = ?", (_now(), _now(), annotation_id))
    else:
        connection.execute(
            """
            UPDATE annotation SET label_code=?, scope=?, start_offset_ns=?, end_offset_ns=?, target_type=?, target_key=?,
                severity=?, action=?, comment=?, attributes_json=?, source=?, status=?, reviewer_name=?, updated_at=?, deleted_at=? WHERE id=?
            """,
            (
                snapshot["label_code"], snapshot["scope"], snapshot["start_offset_ns"], snapshot["end_offset_ns"],
                snapshot["target_type"], snapshot.get("target_key"), snapshot.get("severity"), snapshot.get("action"),
                snapshot.get("comment"), _json(snapshot.get("attributes", {})), snapshot.get("source", "manual"),
                snapshot.get("status", "confirmed"), snapshot.get("reviewer_name", ""), _now(), snapshot.get("deleted_at"), annotation_id,
            ),
        )
    if episode_id:
        _refresh_annotation_count(connection, str(episode_id))


def _refresh_annotation_count(connection: sqlite3.Connection, episode_id: str) -> None:
    count = connection.execute(
        "SELECT COUNT(*) FROM annotation WHERE episode_id = ? AND deleted_at IS NULL", (episode_id,)
    ).fetchone()[0]
    connection.execute("UPDATE episode SET annotation_count = ?, updated_at = ? WHERE id = ?", (count, _now(), episode_id))


def update_episode_review(
    db_path: str | Path,
    episode_id: str,
    *,
    review_status: str | None = None,
    quality_decision: str | None = None,
    reviewer_name: str | None = None,
    last_playhead_ns: int | None = None,
) -> dict[str, object]:
    if review_status is not None and review_status not in VALID_REVIEW_STATUSES:
        raise ValueError(f"无效质检状态: {review_status}")
    if quality_decision is not None and quality_decision not in VALID_QUALITY_DECISIONS:
        raise ValueError(f"无效质量结论: {quality_decision}")
    with connect_workspace(db_path) as connection:
        row = connection.execute("SELECT * FROM episode WHERE id = ?", (episode_id,)).fetchone()
        if not row:
            raise KeyError(f"Episode 不存在: {episode_id}")
        status = review_status if review_status is not None else row["review_status"]
        decision = quality_decision if quality_decision is not None else row["quality_decision"]
        reviewer = reviewer_name if reviewer_name is not None else row["reviewer_name"]
        playhead = int(last_playhead_ns if last_playhead_ns is not None else row["last_playhead_ns"])
        duration = int(row["duration_ns"] or 0)
        playhead = max(0, min(playhead, duration))
        reviewed_at = _now() if status in {"completed", "reviewed"} else row["reviewed_at"]
        connection.execute(
            "UPDATE episode SET review_status=?, quality_decision=?, reviewer_name=?, last_playhead_ns=?, reviewed_at=?, updated_at=? WHERE id=?",
            (status, decision, reviewer, playhead, reviewed_at, _now(), episode_id),
        )
        task = connection.execute(
            "SELECT ds.task_id FROM data_source ds WHERE ds.id = ?",
            (row["data_source_id"],),
        ).fetchone()
        if task and task["task_id"]:
            _refresh_task_status(connection, str(task["task_id"]))
    return episode_detail(db_path, episode_id)["episode"]


def export_workspace(
    db_path: str | Path,
    output_parent: str | Path,
    *,
    episode_ids: list[str] | None = None,
    completed_only: bool = False,
    export_format: str = "json",
    task_id: str | None = None,
) -> dict[str, object]:
    export_format = str(export_format).strip().lower()
    if export_format not in {"csv", "json"}:
        raise ValueError("导出格式只支持 csv 或 json")
    initialize_workspace(db_path)
    output_root = Path(output_parent).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    filters = {
        "episode_ids": episode_ids or [],
        "completed_only": completed_only,
        "format": export_format,
        "task_id": task_id,
    }
    with connect_workspace(db_path) as connection:
        workspace = dict(connection.execute("SELECT * FROM workspace LIMIT 1").fetchone())
        selected_task = _task_row(connection, task_id) if task_id else None
        where_parts: list[str] = []
        parameters: list[object] = []
        if task_id:
            where_parts.append("ds.task_id = ?")
            parameters.append(task_id)
        if episode_ids:
            where_parts.append(f"e.id IN ({','.join('?' for _ in episode_ids)})")
            parameters.extend(episode_ids)
        if completed_only:
            where_parts.append("e.review_status IN ('completed', 'reviewed')")
        where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
        episodes = _episode_rows(connection, where, tuple(parameters))
        if episode_ids and len(episodes) != len(set(episode_ids)):
            raise ValueError("导出列表包含不属于当前任务或不存在的 Episode")
        selected_ids = [str(item["id"]) for item in episodes]
        annotations: list[dict[str, object]] = []
        if selected_ids:
            query = f"""
                SELECT a.*, e.episode_name, e.start_time_ns AS episode_start_time_ns,
                       e.duration_ns AS episode_duration_ns, e.relative_path,
                       ds.root_path AS source_root, ld.name AS label_name
                FROM annotation a
                JOIN episode e ON e.id = a.episode_id
                JOIN data_source ds ON ds.id = e.data_source_id
                LEFT JOIN label_set ls ON ls.label_set_key = a.label_set_key AND ls.version = a.label_schema_version
                LEFT JOIN label_definition ld ON ld.label_set_id = ls.id AND ld.code = a.label_code
                WHERE a.deleted_at IS NULL AND a.episode_id IN ({','.join('?' for _ in selected_ids)})
                ORDER BY e.relative_path, a.start_offset_ns, a.created_at
            """
            annotations = [_export_annotation_row(row) for row in connection.execute(query, selected_ids)]
        schema = _active_label_schema(connection) or {}

    if not episodes:
        raise ValueError("当前筛选没有可导出的 Episode")

    exported_at = _now()
    episode_rows = [_export_episode_row(item) for item in episodes]
    schema_header = schema.get("schema") if isinstance(schema.get("schema"), dict) else {}
    common_info = {
        "export_version": "2.0.0",
        "application_version": APP_VERSION,
        "workspace_id": workspace["id"],
        "label_set_id": schema_header.get("label_set_id"),
        "label_schema_version": schema_header.get("schema_version"),
        "exported_at": exported_at,
        "filters": filters,
        "format": export_format,
    }

    episodes_by_source: dict[str, list[dict[str, object]]] = {}
    for episode in episode_rows:
        episodes_by_source.setdefault(str(episode["source_root"]), []).append(episode)
    annotations_by_source: dict[str, list[dict[str, object]]] = {}
    for annotation in annotations:
        annotations_by_source.setdefault(str(annotation["source_root"]), []).append(annotation)

    safe_stems = [_safe_export_file_stem(_export_task_name([root])) for root in episodes_by_source]
    duplicate_stems = {stem for stem in safe_stems if safe_stems.count(stem) > 1}
    task_results: list[dict[str, object]] = []
    export_records: list[tuple[dict[str, object], Path]] = []
    for source_root, task_episodes in episodes_by_source.items():
        task_annotations = annotations_by_source.get(source_root, [])
        task_name = str(selected_task["task_name"]) if selected_task else _export_task_name([source_root])
        export_stem = _safe_export_file_stem(task_name)
        if export_stem in duplicate_stems:
            export_stem = f"{export_stem}_{hashlib.sha256(source_root.encode('utf-8')).hexdigest()[:8]}"
        final_file = output_root / f"{export_stem}_标注结果.{export_format}"
        temporary_file = output_root / f".{final_file.name}.{uuid.uuid4().hex}.tmp"
        task_filters = filters | {"source_directory": source_root}
        task_info = common_info | {
            "task_id": str(selected_task["id"]) if selected_task else None,
            "task_code": str(selected_task["task_code"]) if selected_task else None,
            "task_name": task_name,
            "filters": task_filters,
            "source_directories": [source_root],
            "episode_count": len(task_episodes),
            "annotation_count": len(task_annotations),
        }
        try:
            if export_format == "json":
                document = task_info | {
                    "label_schema": schema,
                    "episodes": task_episodes,
                    "annotations": task_annotations,
                }
                temporary_file.write_text(
                    json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            else:
                csv_rows = _single_file_csv_rows(
                    task_episodes,
                    task_annotations,
                    task_name=task_name,
                    exported_at=exported_at,
                )
                _write_csv(temporary_file, _single_file_csv_fields(), csv_rows)
            temporary_file.replace(final_file)
        finally:
            temporary_file.unlink(missing_ok=True)
        task_result = task_info | {
            "output_file": str(final_file),
            "file_name": final_file.name,
        }
        task_results.append(task_result)
        export_records.append((task_filters, final_file))

    with connect_workspace(db_path) as connection:
        for task_result, (task_filters, final_file) in zip(task_results, export_records):
            connection.execute(
                "INSERT INTO export_record VALUES (?, ?, ?, ?, ?, ?, 'completed', NULL, ?)",
                (
                    _new_id("exp"),
                    workspace["id"],
                    _json(task_filters),
                    str(final_file),
                    task_result["episode_count"],
                    task_result["annotation_count"],
                    _now(),
                ),
            )

    result = common_info | {
        "task_count": len(task_results),
        "episode_count": len(episodes),
        "annotation_count": len(annotations),
        "tasks": task_results,
        "output_files": [str(item[1]) for item in export_records],
        "output_dir": str(output_root),
    }
    if len(task_results) == 1:
        result |= {
            "task_id": task_results[0]["task_id"],
            "task_code": task_results[0]["task_code"],
            "task_name": task_results[0]["task_name"],
            "source_directories": task_results[0]["source_directories"],
            "output_file": task_results[0]["output_file"],
            "file_name": task_results[0]["file_name"],
        }
    return result


def _export_task_name(source_roots: list[str]) -> str:
    names = list(dict.fromkeys(Path(root).name for root in source_roots if Path(root).name))
    if not names:
        return "Episode_QC_任务"
    return names[0] if len(names) == 1 else f"{names[0]}_等{len(names)}个任务"


def _safe_export_file_stem(task_name: str) -> str:
    """保留可读任务名，只替换 Windows/macOS/Linux 文件名均不安全的字符。"""
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", task_name).strip(" .")
    return (safe_name or "Episode_QC_任务")[:96].rstrip(" .")


def _export_annotation_row(row: sqlite3.Row) -> dict[str, object]:
    start = int(row["start_offset_ns"])
    end = int(row["end_offset_ns"])
    absolute_base = int(row["episode_start_time_ns"] or 0)
    return {
        "annotation_id": row["id"],
        "episode_id": row["episode_id"],
        "source_root": row["source_root"],
        "relative_episode_path": row["relative_path"],
        "episode_name": row["episode_name"],
        "episode_start_time_ns": row["episode_start_time_ns"],
        "episode_duration_ns": row["episode_duration_ns"],
        "label_set_id": row["label_set_key"],
        "label_schema_version": row["label_schema_version"],
        "label_code": row["label_code"],
        "label_name": row["label_name"] or row["label_code"],
        "scope": row["scope"],
        "start_offset_ns": start,
        "end_offset_ns": end,
        "start_sec": start / 1e9,
        "end_sec": end / 1e9,
        "absolute_start_time_ns": absolute_base + start,
        "absolute_end_time_ns": absolute_base + end,
        "target_type": row["target_type"],
        "target_key": row["target_key"],
        "severity": row["severity"],
        "action": row["action"],
        "comment": row["comment"],
        "attributes_json": row["attributes_json"],
        "reviewer": row["reviewer_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _export_episode_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "episode_id": row["id"],
        "source_root": row["source_root"],
        "relative_episode_path": row["relative_path"],
        "episode_name": row["episode_name"],
        "data_group": row["data_group"],
        "start_time_ns": row["start_time_ns"],
        "end_time_ns": row["end_time_ns"],
        "duration_sec": row["duration_sec"],
        "camera_count": row["camera_count"],
        "mocap_available": row["mocap_available"],
        "import_status": row["import_status"],
        "review_status": row["review_status"],
        "quality_decision": row["quality_decision"],
        "annotation_count": row["annotation_count"],
        "reviewer": row["reviewer_name"],
        "reviewed_at": row["reviewed_at"],
        "source_fingerprint": row["fingerprint"],
    }


def _single_file_csv_fields() -> list[str]:
    return [
        "task_name", "exported_at", *_episode_export_fields(),
        "annotation_id", "label_set_id", "label_schema_version", "label_code", "label_name",
        "scope", "start_offset_ns", "end_offset_ns", "start_sec", "end_sec",
        "absolute_start_time_ns", "absolute_end_time_ns", "target_type", "target_key",
        "severity", "action", "comment", "attributes_json", "annotation_reviewer",
        "annotation_created_at", "annotation_updated_at",
    ]


def _single_file_csv_rows(
    episodes: list[dict[str, object]],
    annotations: list[dict[str, object]],
    *,
    task_name: str,
    exported_at: str,
) -> list[dict[str, object]]:
    annotations_by_episode: dict[str, list[dict[str, object]]] = {}
    for annotation in annotations:
        annotations_by_episode.setdefault(str(annotation["episode_id"]), []).append(annotation)

    annotation_fields = [
        "annotation_id", "label_set_id", "label_schema_version", "label_code", "label_name",
        "scope", "start_offset_ns", "end_offset_ns", "start_sec", "end_sec",
        "absolute_start_time_ns", "absolute_end_time_ns", "target_type", "target_key",
        "severity", "action", "comment", "attributes_json",
    ]
    rows: list[dict[str, object]] = []
    for episode in episodes:
        episode_annotations = annotations_by_episode.get(str(episode["episode_id"])) or [None]
        for annotation in episode_annotations:
            row = {"task_name": task_name, "exported_at": exported_at, **episode}
            if annotation is not None:
                row.update({field: annotation.get(field) for field in annotation_fields})
                row["annotation_reviewer"] = annotation.get("reviewer")
                row["annotation_created_at"] = annotation.get("created_at")
                row["annotation_updated_at"] = annotation.get("updated_at")
            rows.append(row)
    return rows


def _episode_export_fields() -> list[str]:
    return [
        "episode_id", "source_root", "relative_episode_path", "episode_name", "data_group",
        "start_time_ns", "end_time_ns", "duration_sec", "camera_count", "mocap_available",
        "import_status", "review_status", "quality_decision", "annotation_count", "reviewer",
        "reviewed_at", "source_fingerprint",
    ]


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
