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
from episode_qc.source_paths import resolve_source_directory


SCHEMA_VERSION = 7
TASK_KINDS = {"robot_teleoperation", "ego_omniego"}
ANNOTATION_MODES = {"library", "open"}
OPEN_ANNOTATION_TYPES = {"action", "pose_quality", "camera_quality", "exception", "object_state", "other"}
EGO_OPEN_SCHEMA_VERSION = "ego_open_v1"
EPISODE_INDEX_VERSION = 2


class WorkspaceConflictError(RuntimeError):
    """Raised when a browser tries to overwrite a record changed in another tab."""


APP_VERSION = "1.0.0"
WORKSPACE_BUSY_TIMEOUT_MS = 30_000
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
    connection = sqlite3.connect(
        path,
        timeout=WORKSPACE_BUSY_TIMEOUT_MS / 1000,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {WORKSPACE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def backup_workspace_database(
    db_path: str | Path,
    backup_dir: str | Path,
    *,
    reason: str = "manual",
) -> Path:
    """Create a transactionally consistent SQLite backup of the QC workspace."""
    source_path = Path(db_path).expanduser().resolve()
    initialize_workspace(source_path)
    destination_dir = Path(backup_dir).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^A-Za-z0-9._-]+", "-", str(reason)).strip("-._") or "manual"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_dir / f"workspace-{timestamp}-{safe_reason}.db"
    with connect_workspace(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    destination.chmod(0o600)
    return destination


def initialize_workspace(
    db_path: str | Path,
    *,
    name: str = "Mocap QC 工作区",
    reviewer_name: str = "",
) -> dict[str, object]:
    with connect_workspace(db_path) as connection:
        return _initialize_workspace(
            connection,
            name=name,
            reviewer_name=reviewer_name,
        )


def _initialize_workspace(
    connection: sqlite3.Connection,
    *,
    name: str = "Mocap QC 工作区",
    reviewer_name: str = "",
) -> dict[str, object]:
    _execute_schema_statements(
        connection,
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
                label_set_id TEXT REFERENCES label_set(id),
                source_uri TEXT NOT NULL,
                local_source_path TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'server_path',
                task_kind TEXT NOT NULL DEFAULT 'robot_teleoperation',
                annotation_mode TEXT NOT NULL DEFAULT 'library',
                annotation_schema_version TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'importing',
                import_error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                last_episode_id TEXT,
                review_started_at TEXT,
                review_completed_at TEXT,
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
                index_version INTEGER NOT NULL DEFAULT 1,
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
                previous_review_json TEXT,
                review_history_count INTEGER NOT NULL DEFAULT 0,
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
                annotation_mode TEXT NOT NULL DEFAULT 'library',
                annotation_schema_version TEXT NOT NULL DEFAULT '',
                annotation_type TEXT NOT NULL DEFAULT 'quality',
                label_name TEXT NOT NULL DEFAULT '',
                label_slug TEXT NOT NULL DEFAULT '',
                label_snapshot_json TEXT NOT NULL DEFAULT '{}',
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
            """,
    )
    _ensure_column(connection, "data_source", "task_id", "TEXT REFERENCES qc_task(id)")
    _ensure_column(connection, "qc_task", "source_type", "TEXT NOT NULL DEFAULT 'server_path'")
    _ensure_column(
        connection,
        "qc_task",
        "task_kind",
        "TEXT NOT NULL DEFAULT 'robot_teleoperation'",
    )
    _ensure_column(connection, "qc_task", "label_set_id", "TEXT REFERENCES label_set(id)")
    _ensure_column(connection, "qc_task", "annotation_mode", "TEXT NOT NULL DEFAULT 'library'")
    _ensure_column(connection, "qc_task", "annotation_schema_version", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "qc_task", "review_started_at", "TEXT")
    _ensure_column(connection, "qc_task", "review_completed_at", "TEXT")
    _ensure_column(connection, "episode", "index_version", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(connection, "episode", "previous_review_json", "TEXT")
    _ensure_column(connection, "annotation", "annotation_mode", "TEXT NOT NULL DEFAULT 'library'")
    _ensure_column(connection, "annotation", "annotation_schema_version", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "annotation", "annotation_type", "TEXT NOT NULL DEFAULT 'quality'")
    _ensure_column(connection, "annotation", "label_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "annotation", "label_slug", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "annotation", "label_snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(
        connection,
        "episode",
        "review_history_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    row = connection.execute("SELECT * FROM workspace LIMIT 1").fetchone()
    if row is None:
        workspace_id = _new_id("ws")
        now = _now()
        connection.execute(
            "INSERT INTO workspace VALUES (?, ?, ?, NULL, '{}', ?, ?, ?)",
            (workspace_id, name, reviewer_name, SCHEMA_VERSION, now, now),
        )
        previous_schema_version = SCHEMA_VERSION
    else:
        previous_schema_version = int(row["schema_version"] or 0)
    _migrate_data_sources_to_tasks(connection)
    _upgrade_inferred_platform_tasks(connection)
    _migrate_last_episode_to_task(connection)
    if previous_schema_version < 7:
        connection.execute(
            """
            UPDATE qc_task
            SET annotation_mode = 'open', annotation_schema_version = ?
            WHERE task_kind = 'ego_omniego' AND annotation_mode = 'library'
            """,
            (EGO_OPEN_SCHEMA_VERSION,),
        )
    connection.execute(
        "UPDATE qc_task SET label_set_id = NULL WHERE annotation_mode = 'open' AND label_set_id IS NOT NULL"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_data_source_task ON data_source(task_id) WHERE task_id IS NOT NULL"
    )
    connection.execute("UPDATE workspace SET schema_version = ?", (SCHEMA_VERSION,))
    row = connection.execute("SELECT * FROM workspace LIMIT 1").fetchone()
    return dict(row)


def _execute_schema_statements(connection: sqlite3.Connection, script: str) -> None:
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("工作区 schema SQL 不完整")


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
    label_set_id: str | None = None,
    source_uri: str | None = None,
    task_metadata: dict[str, object] | None = None,
    task_kind: str = "robot_teleoperation",
    annotation_mode: str | None = None,
    annotation_schema_version: str | None = None,
) -> dict[str, object]:
    initialize_workspace(db_path)
    if task_kind not in TASK_KINDS:
        raise ValueError(f"不支持的 QC 任务类型: {task_kind}")
    effective_annotation_mode = str(
        annotation_mode or ("open" if task_kind == "ego_omniego" else "library")
    ).strip()
    if effective_annotation_mode not in ANNOTATION_MODES:
        raise ValueError(f"不支持的标注模式: {effective_annotation_mode}")
    effective_annotation_schema_version = str(
        annotation_schema_version
        or (EGO_OPEN_SCHEMA_VERSION if effective_annotation_mode == "open" else "")
    ).strip()
    if effective_annotation_mode == "open" and not effective_annotation_schema_version:
        raise ValueError("开放标注模式必须声明结构版本")
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
        if label_set_id is not None:
            label_set = connection.execute(
                "SELECT id FROM label_set WHERE id = ? AND enabled = 1",
                (label_set_id,),
            ).fetchone()
            if label_set is None:
                raise ValueError(f"QC 任务标签库不存在或已停用: {label_set_id}")
            if (
                existing_task is not None
                and existing_task["label_set_id"]
                and str(existing_task["label_set_id"]) != str(label_set_id)
            ):
                raise ValueError("同一 QC 任务不能改绑到不同标签版本")
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
                label_set_id, source_uri, local_source_path, status, import_error, metadata_json,
                task_kind, annotation_mode, annotation_schema_version,
                last_episode_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'importing', NULL, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task_code = excluded.task_code,
                task_name = excluded.task_name,
                origin = excluded.origin,
                flow_job_code = COALESCE(excluded.flow_job_code, qc_task.flow_job_code),
                asset_id = COALESCE(excluded.asset_id, qc_task.asset_id),
                label_set_id = COALESCE(excluded.label_set_id, qc_task.label_set_id),
                source_uri = excluded.source_uri,
                local_source_path = excluded.local_source_path,
                status = 'importing',
                import_error = NULL,
                metadata_json = excluded.metadata_json,
                task_kind = excluded.task_kind,
                annotation_mode = excluded.annotation_mode,
                annotation_schema_version = excluded.annotation_schema_version,
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
                label_set_id,
                effective_source_uri,
                str(root),
                _json(metadata or {}),
                task_kind,
                effective_annotation_mode,
                effective_annotation_schema_version,
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

    candidates = _discover_episode_mcaps(root, profile, task_kind=task_kind)
    indexed: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    with connect_workspace(db_path) as connection:
        for mcap_path in candidates:
            if task_kind == "ego_omniego":
                relative_path = str(mcap_path.relative_to(root))
            else:
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
        restored_annotations, restored_episode_states, import_warnings = _restore_task_annotations(
            connection,
            source_id,
            root,
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
        "restored_annotations": restored_annotations,
        "restored_episode_states": restored_episode_states,
        "import_warnings": import_warnings,
        "episodes": indexed,
    }


def _discover_episode_mcaps(
    root: Path,
    profile: dict[str, object],
    *,
    task_kind: str = "robot_teleoperation",
) -> list[Path]:
    import_config = profile.get("import") if isinstance(profile.get("import"), dict) else {}
    episode_patterns = list(import_config.get("episode_directory_patterns") or ["episode_*"])
    mcap_patterns = list(import_config.get("mcap_file_patterns") or ["episode.mcap", "*.mcap"])
    bvh_patterns = list(import_config.get("bvh_file_patterns") or ["motion.bvh", "*.bvh"])
    if task_kind == "ego_omniego":
        return sorted(
            (path.resolve() for path in root.rglob("*.mcap") if path.is_file()),
            key=lambda item: _natural_key(str(item.relative_to(root))),
        )
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


def _discover_annotation_result_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    discovered: list[Path] = []
    seen: set[str] = set()
    preferred = root / "qc_result.json"
    if preferred.is_file():
        discovered.append(preferred)
        seen.add(str(preferred))
    for path in sorted(root.glob("*标注结果.json")):
        if path.is_file() and str(path) not in seen:
            discovered.append(path)
            seen.add(str(path))
    return discovered


def _normalize_relative_episode_path(raw_path: object) -> str | None:
    if not raw_path:
        return None
    if not isinstance(raw_path, str):
        return None
    value = raw_path.strip().replace("\\", "/")
    if not value:
        return None
    if value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        return None
    while value.startswith("./"):
        value = value[2:].lstrip("/")
    value = value.lstrip("./")
    if not value:
        return None
    return str(Path(value).as_posix())


def _extract_annotations_for_import(document: object) -> list[dict[str, object]]:
    if not isinstance(document, dict):
        return []
    raw_annotations = document.get("annotations")
    if isinstance(raw_annotations, list):
        return [item for item in raw_annotations if isinstance(item, dict)]
    return []


def _extract_episodes_for_import(document: object) -> list[dict[str, object]]:
    if not isinstance(document, dict):
        return []
    raw_episodes = document.get("episodes")
    if isinstance(raw_episodes, list):
        return [item for item in raw_episodes if isinstance(item, dict)]
    return []


def _normalize_imported_annotation(item: dict[str, object]) -> dict[str, object]:
    value = dict(item)
    if "attributes" not in value and "attributes_json" in value:
        raw_attributes = value["attributes_json"]
        if not isinstance(raw_attributes, str):
            raise ValueError("attributes_json 必须是 JSON 字符串")
        attributes = _loads(raw_attributes, None)
        if not isinstance(attributes, dict):
            raise ValueError("attributes_json 必须解析为对象")
        value["attributes"] = attributes
    if "reviewer_name" not in value and "reviewer" in value:
        value["reviewer_name"] = str(value.get("reviewer") or "")
    return value


def _match_import_episode(
    episode_by_path: dict[str, sqlite3.Row],
    episode_by_id: dict[str, sqlite3.Row],
    item: dict[str, object],
) -> sqlite3.Row | None:
    relative_path = _normalize_relative_episode_path(
        item.get("relative_episode_path") or item.get("relative_path")
    )
    if relative_path is not None:
        episode = episode_by_path.get(relative_path)
        if episode is not None:
            return episode
    return episode_by_id.get(str(item.get("episode_id") or ""))


def _restore_task_episode_state(
    connection: sqlite3.Connection,
    episode_by_path: dict[str, sqlite3.Row],
    episode_by_id: dict[str, sqlite3.Row],
    item: dict[str, object],
) -> str | None:
    episode = _match_import_episode(episode_by_path, episode_by_id, item)
    if episode is None:
        raise ValueError(
            f"未在本任务中匹配到状态关联的 Episode：{item.get('relative_episode_path') or item.get('relative_path') or item.get('episode_id')}"
        )

    review_status = str(item.get("review_status") or "").strip()
    if not review_status or review_status == "unreviewed":
        return None
    if review_status not in VALID_REVIEW_STATUSES:
        raise ValueError(f"无效质检状态: {review_status}")

    # Restore prior progress into a fresh task without overwriting work that has
    # already advanced locally after the result file was produced.
    current_episode = connection.execute(
        "SELECT review_status FROM episode WHERE id = ?",
        (episode["id"],),
    ).fetchone()
    if current_episode is None or str(current_episode["review_status"]) != "unreviewed":
        return None

    raw_decision = item.get("quality_decision")
    quality_decision = str(raw_decision).strip() if raw_decision not in {None, ""} else None
    if quality_decision is not None and quality_decision not in VALID_QUALITY_DECISIONS:
        raise ValueError(f"无效质量结论: {quality_decision}")

    reviewer_name = str(item.get("reviewer_name") or item.get("reviewer") or "")
    imported_reviewed_at = str(item.get("reviewed_at") or "").strip() or None
    if review_status in {"completed", "reviewed"}:
        reviewed_at = imported_reviewed_at or _now()
    else:
        reviewed_at = imported_reviewed_at
    now = _now()
    connection.execute(
        """
        UPDATE episode
        SET review_status = ?, quality_decision = ?, reviewer_name = ?,
            reviewed_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            review_status,
            quality_decision,
            reviewer_name,
            reviewed_at,
            now,
            episode["id"],
        ),
    )
    return str(episode["id"])


def _resolve_import_annotation_id(
    connection: sqlite3.Connection,
    episode_id: str,
    source_episode_id: str,
    annotation_id: str,
) -> str:
    candidate_id = (
        annotation_id
        if source_episode_id == episode_id
        else _stable_id("ann", episode_id, annotation_id)
    )
    existing = connection.execute(
        "SELECT episode_id FROM annotation WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if existing is not None and str(existing["episode_id"]) != episode_id:
        raise ValueError(f"标注 ID 冲突: {annotation_id}")
    return candidate_id


def _restore_task_annotation(connection: sqlite3.Connection, episode_by_path: dict[str, sqlite3.Row], episode_by_id: dict[str, sqlite3.Row], item: dict[str, object]) -> str:
    item = _normalize_imported_annotation(item)
    episode = _match_import_episode(episode_by_path, episode_by_id, item)
    if episode is None:
        raise ValueError(
            f"未在本任务中匹配到标注关联的 Episode：{item.get('relative_episode_path') or item.get('relative_path') or item.get('episode_id')}"
        )

    annotation_id = str(item.get("annotation_id") or item.get("id") or "").strip()
    if not annotation_id:
        annotation_id = _new_id("ann")
    annotation_id = _resolve_import_annotation_id(
        connection,
        str(episode["id"]),
        str(item.get("episode_id") or ""),
        annotation_id,
    )
    label_set_id = str(item.get("label_set_id") or item.get("label_set_key") or "").strip()
    label_schema_version = str(item.get("label_schema_version") or "").strip()
    label_code = str(item.get("label_code") or "").strip()
    if not label_set_id or not label_schema_version or not label_code:
        raise ValueError("标注缺少 label_set_id、label_schema_version 或 label_code")
    label = connection.execute(
        """
        SELECT ld.*
        FROM label_set ls
        JOIN label_definition ld ON ld.label_set_id = ls.id
        WHERE ls.label_set_key = ? AND ls.version = ? AND ld.code = ?
        """,
        (label_set_id, label_schema_version, label_code),
    ).fetchone()
    if label is None:
        raise ValueError(f"标签不存在或未激活: {label_code} ({label_set_id}@{label_schema_version})")

    normalized = _validate_annotation_payload(dict(item), episode, label)
    now = _now()
    connection.execute(
        """
        INSERT INTO annotation(
            id, episode_id, label_set_key, label_schema_version, label_code, scope,
            start_offset_ns, end_offset_ns, target_type, target_key, severity, action,
            comment, attributes_json, source, status, reviewer_name, created_at, updated_at, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET
            label_set_key = excluded.label_set_key,
            label_schema_version = excluded.label_schema_version,
            label_code = excluded.label_code,
            scope = excluded.scope,
            start_offset_ns = excluded.start_offset_ns,
            end_offset_ns = excluded.end_offset_ns,
            target_type = excluded.target_type,
            target_key = excluded.target_key,
            severity = excluded.severity,
            action = excluded.action,
            comment = excluded.comment,
            attributes_json = excluded.attributes_json,
            source = excluded.source,
            status = excluded.status,
            reviewer_name = excluded.reviewer_name,
            updated_at = excluded.updated_at,
            deleted_at = NULL,
            created_at = annotation.created_at
        """,
        (
            annotation_id,
            episode["id"],
            label_set_id,
            label_schema_version,
            label_code,
            normalized["scope"],
            int(normalized["start_offset_ns"]),
            int(normalized["end_offset_ns"]),
            normalized["target_type"],
            normalized.get("target_key"),
            normalized.get("severity") or label["default_severity"],
            normalized.get("action") or label["default_action"],
            normalized.get("comment", ""),
            _json(normalized.get("attributes", {})),
            "imported",
            normalized.get("status", "confirmed"),
            str(normalized.get("reviewer_name") or ""),
            str(item.get("created_at") or now),
            now,
        ),
    )
    current_episode = connection.execute(
        "SELECT review_status FROM episode WHERE id = ?",
        (episode["id"],),
    ).fetchone()
    if current_episode is not None and current_episode["review_status"] == "unreviewed":
        connection.execute(
            "UPDATE episode SET review_status = 'in_progress', updated_at = ? WHERE id = ?",
            (now, episode["id"]),
        )
    _refresh_annotation_count(connection, episode["id"])
    return str(episode["id"])


def _restore_task_annotations(
    connection: sqlite3.Connection,
    source_id: str,
    root_path: Path,
) -> tuple[int, int, list[str]]:
    result_files = _discover_annotation_result_files(root_path)
    if not result_files:
        return 0, 0, []
    episode_rows = connection.execute(
        "SELECT * FROM episode WHERE data_source_id = ?",
        (source_id,),
    ).fetchall()
    if not episode_rows:
        return 0, 0, []
    episode_by_path: dict[str, sqlite3.Row] = {}
    for row in episode_rows:
        relative_path = _normalize_relative_episode_path(row["relative_path"])
        if relative_path is None:
            continue
        episode_by_path[relative_path] = row
        prefixed_path = _normalize_relative_episode_path(
            f"{root_path.name}/{relative_path}"
        )
        if prefixed_path is not None:
            episode_by_path.setdefault(prefixed_path, row)
    episode_by_id: dict[str, sqlite3.Row] = {str(row["id"]): row for row in episode_rows}
    restored_annotations = 0
    restored_episode_states = 0
    import_warnings: list[str] = []
    for path in result_files:
        try:
            document = _loads(path.read_text(encoding="utf-8"), None)
        except (OSError, json.JSONDecodeError) as exc:
            import_warnings.append(f"{path.name} 读取失败：{exc}")
            continue
        episodes = _extract_episodes_for_import(document)
        for index, item in enumerate(episodes, start=1):
            try:
                restored_episode_id = _restore_task_episode_state(
                    connection,
                    episode_by_path,
                    episode_by_id,
                    item,
                )
                if restored_episode_id is not None:
                    restored_episode_states += 1
            except Exception as exc:
                import_warnings.append(
                    f"{path.name} Episode#{index} 状态恢复失败：{exc}"
                )
        annotations = _extract_annotations_for_import(document)
        for index, item in enumerate(annotations, start=1):
            try:
                _restore_task_annotation(connection, episode_by_path, episode_by_id, item)
                restored_annotations += 1
            except Exception as exc:
                import_warnings.append(
                    f"{path.name} 标注#{index} 恢复失败：{exc}"
                )
    return restored_annotations, restored_episode_states, import_warnings


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
        and int(old["index_version"] or 0) == EPISODE_INDEX_VERSION
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
        _json([
            EPISODE_INDEX_VERSION,
            source_id,
            relative_path,
            stat.st_size,
            stat.st_mtime_ns,
            start_ns,
            end_ns,
        ]).encode("utf-8")
    ).hexdigest()
    relative = Path(relative_path)
    data_group = root.name if relative.parent == Path(".") else relative.parts[0]
    episode_name = mcap_path.stem if relative.suffix.lower() in {".mcap", ".bvh"} else mcap_path.parent.name
    now = _now()
    import_status = "ready" if error is None else "failed"

    cache_status = "stale" if old and old["fingerprint"] != fingerprint else "not_prepared"
    connection.execute(
            """
            INSERT INTO episode(
                id, data_source_id, relative_path, episode_name, data_group, mcap_path,
                summary_path, config_path, fingerprint, index_version, file_size, file_mtime_ns,
                start_time_ns, end_time_ns, duration_ns, import_status, import_error,
                cache_status, review_status, quality_decision, reviewer_name,
                last_playhead_ns, annotation_count, created_at, updated_at, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed', NULL, NULL, 0, 0, ?, ?, NULL)
            ON CONFLICT(data_source_id, relative_path) DO UPDATE SET
                episode_name = excluded.episode_name,
                data_group = excluded.data_group,
                mcap_path = excluded.mcap_path,
                summary_path = excluded.summary_path,
                config_path = excluded.config_path,
                fingerprint = excluded.fingerprint,
                index_version = excluded.index_version,
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
                episode_name,
                data_group,
                str(mcap_path),
                str(summary_path) if summary_path else None,
                str(config_path) if config_path else None,
                fingerprint,
                EPISODE_INDEX_VERSION,
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
        "episode_name": episode_name,
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
    if (
        schema_name in {"foxglove.CompressedImage", "sensor_msgs/CompressedImage"}
        or (("camera" in topic or "/cam" in topic or "/t265_" in topic) and "image" in topic)
    ):
        adapter = "ros1_compressed_image_v1" if schema_name == "sensor_msgs/CompressedImage" else "foxglove_compressed_image_v1"
        return "camera", "camera", _camera_name(topic), adapter, "jpeg"
    if topic == "/dohc/skeleton":
        return "mocap", "human_pose", "人体 Pose 骨架", "dohc_smpl_24_v1", message_encoding
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
    if len(parts) >= 3 and parts[0] == "dohc" and "image" in parts:
        return {
            "cam0": "DOHC Cam 0",
            "cam1": "DOHC Cam 1",
            "cam2": "DOHC Cam 2",
            "t265_left": "T265 左目",
            "t265_right": "T265 右目",
        }.get(parts[1], parts[1])
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
               t.source_type, t.task_kind,
               SUM(CASE WHEN s.stream_type = 'camera' AND s.available = 1 THEN 1 ELSE 0 END) AS camera_count,
               MAX(CASE WHEN s.stream_type = 'mocap' AND s.available = 1 THEN 1 ELSE 0 END) AS mocap_available,
               COALESCE(changes.incremental_added_count, 0) AS incremental_added_count,
               COALESCE(changes.incremental_modified_count, 0) AS incremental_modified_count,
               COALESCE(changes.incremental_removed_count, 0) AS incremental_removed_count,
               COALESCE(changes.incremental_preserved_count, 0) AS incremental_preserved_count
        FROM episode e
        JOIN data_source ds ON ds.id = e.data_source_id
        JOIN qc_task t ON t.id = ds.task_id
        LEFT JOIN stream s ON s.episode_id = e.id
        LEFT JOIN (
            SELECT episode_id,
                   SUM(CASE WHEN deleted_at IS NULL AND source != 'flow_incremental' THEN 1 ELSE 0 END) AS incremental_added_count,
                   SUM(CASE WHEN deleted_at IS NULL AND source = 'flow_incremental' AND updated_at != created_at THEN 1 ELSE 0 END) AS incremental_modified_count,
                   SUM(CASE WHEN deleted_at IS NOT NULL AND source = 'flow_incremental' THEN 1 ELSE 0 END) AS incremental_removed_count,
                   SUM(CASE WHEN deleted_at IS NULL AND source = 'flow_incremental' AND updated_at = created_at THEN 1 ELSE 0 END) AS incremental_preserved_count
            FROM annotation
            GROUP BY episode_id
        ) changes ON changes.episode_id = e.id
        {where}
        GROUP BY e.id
        ORDER BY e.data_group COLLATE NOCASE, e.relative_path COLLATE NOCASE
    """
    rows = []
    for row in connection.execute(query, parameters):
        value = dict(row)
        value["camera_count"] = int(value["camera_count"] or 0)
        value["mocap_available"] = bool(value["mocap_available"])
        for key in (
            "incremental_added_count",
            "incremental_modified_count",
            "incremental_removed_count",
            "incremental_preserved_count",
        ):
            value[key] = int(value[key] or 0)
        value["duration_sec"] = (value["duration_ns"] or 0) / 1e9
        value["previous_review"] = _loads(
            value.pop("previous_review_json"), None
        )
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


def _task_id_for_episode(connection: sqlite3.Connection, episode_id: str) -> str | None:
    row = connection.execute(
        """
        SELECT ds.task_id
        FROM episode e
        JOIN data_source ds ON ds.id = e.data_source_id
        WHERE e.id = ?
        """,
        (episode_id,),
    ).fetchone()
    return str(row["task_id"]) if row and row["task_id"] else None


def _mark_task_review_write(
    connection: sqlite3.Connection,
    episode_id: str,
    *,
    now: str | None = None,
) -> None:
    task_id = _task_id_for_episode(connection, episode_id)
    if not task_id:
        return
    now = now or _now()
    connection.execute(
        """
        UPDATE qc_task
        SET review_started_at = COALESCE(review_started_at, ?),
            review_completed_at = CASE WHEN status = 'completed' THEN ? ELSE NULL END,
            updated_at = ?
        WHERE id = ?
        """,
        (now, now, now, task_id),
    )


def _refresh_task_status(
    connection: sqlite3.Connection,
    task_id: str,
    *,
    now: str | None = None,
) -> None:
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
    now = now or _now()
    connection.execute(
        """
        UPDATE qc_task
        SET status = ?,
            review_completed_at = CASE
                WHEN ? = 'completed' AND review_started_at IS NOT NULL
                    THEN COALESCE(review_completed_at, ?)
                ELSE NULL
            END,
            updated_at = ?
        WHERE id = ?
        """,
        (status, status, now, now, task_id),
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
        label_set_id=str(task["label_set_id"]) if task.get("label_set_id") else None,
        source_uri=str(task["source_uri"]),
        task_metadata=task.get("metadata") if isinstance(task.get("metadata"), dict) else None,
        task_kind=str(task.get("task_kind") or "robot_teleoperation"),
        annotation_mode=str(task.get("annotation_mode") or "library"),
        annotation_schema_version=str(task.get("annotation_schema_version") or ""),
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
        label_schema = _label_schema_for_task(connection, task_id)
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
        deleted_annotation_lineages = _deleted_annotation_lineages(
            connection, episode_id
        )
        schema = _label_schema_for_task(connection, str(episode["task_id"]))
    return {
        "episode": episode,
        "streams": streams,
        "annotations": annotations,
        "deleted_annotation_lineages": deleted_annotation_lineages,
        "label_schema": schema,
    }


def _safe_previous_review(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Flow 上一轮质检结果必须是对象")
    allowed_fields = {
        "episode_review_result_id",
        "review_attempt_id",
        "job_code",
        "attempt_version",
        "reviewer_name",
        "completed_at",
        "decision",
        "quality_grade",
        "annotation_count",
        "quality_annotation_count",
        "job_type",
        "deleted_annotation_lineages",
        "round_number",
    }
    safe = {key: value[key] for key in allowed_fields if key in value}
    if "deleted_annotation_lineages" in value:
        deleted_lineages = value["deleted_annotation_lineages"]
        if not isinstance(deleted_lineages, list):
            raise ValueError("Flow 已删除历史标注血缘必须是数组")
        safe["deleted_annotation_lineages"] = [
            lineage.strip()
            for lineage in deleted_lineages
            if isinstance(lineage, str) and lineage.strip()
        ]
    label_set = value.get("label_set")
    if label_set is not None:
        if not isinstance(label_set, dict):
            raise ValueError("Flow 上一轮标签版本必须是对象")
        safe["label_set"] = {
            key: label_set[key]
            for key in ("id", "label_set_id", "schema_version", "schema_hash")
            if key in label_set
        }
    source = value.get("source")
    if source is not None:
        if not isinstance(source, dict):
            raise ValueError("Flow 上一轮来源信息必须是对象")
        safe["source"] = {
            key: source[key]
            for key in (
                "source_type",
                "source_file_name",
                "annotation_record_id",
                "asset_record_id",
                "archive_sha256",
                "result_id",
            )
            if key in source
        }
    raw_annotations = value.get("annotations", [])
    if not isinstance(raw_annotations, list):
        raise ValueError("Flow 上一轮标注必须是数组")
    annotation_fields = (
        "id",
        "source_annotation_id",
        "label_code",
        "label_name",
        "label_group",
        "label_color",
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
    safe["annotations"] = []
    for annotation in raw_annotations:
        if not isinstance(annotation, dict):
            raise ValueError("Flow 上一轮单条标注必须是对象")
        safe["annotations"].append(
            {key: annotation[key] for key in annotation_fields if key in annotation}
        )
    # Enforce that the payload remains JSON-compatible before writing it to
    # the durable local workspace.
    return json.loads(_json(safe))


def _incremental_lineage(review: dict[str, object], annotation: dict[str, object]) -> str:
    attributes = annotation.get("attributes")
    if isinstance(attributes, dict):
        existing = attributes.get("_incremental_lineage_id")
        if isinstance(existing, str) and existing.strip():
            return existing.strip()
    annotation_identity = annotation.get("source_annotation_id") or annotation.get("id")
    return f"{review.get('job_code') or 'history'}:{annotation_identity}"


def _seed_incremental_annotations(
    connection: sqlite3.Connection,
    *,
    job_code: str,
    local_episode_id: str,
    review_history: list[dict[str, object]],
) -> int:
    """Materialize all compatible historical facts as copy-on-write annotations."""

    episode = connection.execute(
        """
        SELECT e.*, t.label_set_id, w.reviewer_name AS workspace_reviewer
        FROM episode e
        JOIN data_source ds ON ds.id = e.data_source_id
        JOIN qc_task t ON t.id = ds.task_id
        CROSS JOIN workspace w
        WHERE e.id = ?
        """,
        (local_episode_id,),
    ).fetchone()
    if episode is None or not episode["label_set_id"]:
        return 0
    label_set = connection.execute(
        "SELECT * FROM label_set WHERE id = ?",
        (episode["label_set_id"],),
    ).fetchone()
    if label_set is None:
        return 0
    definitions = {
        str(row["code"]): row
        for row in connection.execute(
            "SELECT * FROM label_definition WHERE label_set_id = ? AND enabled = 1",
            (episode["label_set_id"],),
        )
    }

    effective: dict[
        str, tuple[dict[str, object], dict[str, object], int]
    ] = {}
    origin_rounds: dict[str, int] = {}
    for review_index, review in enumerate(review_history, start=1):
        try:
            round_number = max(1, int(review.get("round_number") or review_index))
        except (TypeError, ValueError):
            round_number = review_index
        deleted = review.get("deleted_annotation_lineages")
        if isinstance(deleted, list):
            for lineage in deleted:
                if isinstance(lineage, str):
                    effective.pop(lineage, None)
        annotations = review.get("annotations")
        if not isinstance(annotations, list):
            continue
        for annotation in annotations:
            if not isinstance(annotation, dict):
                continue
            lineage = _incremental_lineage(review, annotation)
            origin_rounds.setdefault(lineage, round_number)
            effective[lineage] = (
                review,
                annotation,
                origin_rounds[lineage],
            )

    inserted = 0
    now = _now()
    for lineage, (review, annotation, origin_round_number) in effective.items():
        label_code = str(annotation.get("label_code") or "")
        label = definitions.get(label_code)
        if label is None:
            continue
        attributes = annotation.get("attributes")
        attributes = dict(attributes) if isinstance(attributes, dict) else {}
        attributes["_incremental_lineage_id"] = lineage
        attributes["_incremental_source"] = {
            "job_code": review.get("job_code"),
            "review_attempt_id": review.get("review_attempt_id"),
            "episode_review_result_id": review.get("episode_review_result_id"),
            "annotation_id": annotation.get("id"),
            "round_number": review.get("round_number"),
            "origin_round_number": origin_round_number,
            "schema_version": (review.get("label_set") or {}).get("schema_version")
            if isinstance(review.get("label_set"), dict)
            else None,
        }
        payload = {
            "label_code": label_code,
            "scope": annotation.get("scope"),
            "start_offset_ns": annotation.get("start_offset_ns", 0),
            "end_offset_ns": annotation.get("end_offset_ns", 0),
            "target_type": annotation.get("target_type"),
            "target_key": annotation.get("target_key"),
            "severity": annotation.get("severity"),
            "action": annotation.get("action"),
            "comment": annotation.get("comment") or "",
            "attributes": attributes,
        }
        try:
            normalized = _validate_annotation_payload(payload, episode, label)
        except (TypeError, ValueError):
            continue
        annotation_id = _stable_id(
            "ann", "flow-incremental", job_code, local_episode_id, lineage
        )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO annotation(
                id, episode_id, label_set_key, label_schema_version, label_code, scope,
                start_offset_ns, end_offset_ns, target_type, target_key, severity, action,
                comment, attributes_json, source, status, reviewer_name, created_at,
                updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'flow_incremental',
                      'confirmed', ?, ?, ?, NULL)
            """,
            (
                annotation_id,
                local_episode_id,
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
                episode["workspace_reviewer"] or "",
                now,
                now,
            ),
        )
        inserted += max(0, int(cursor.rowcount or 0))
    _refresh_annotation_count(connection, local_episode_id)
    return inserted


def sync_flow_previous_reviews(
    db_path: str | Path,
    job: dict[str, object],
    mappings: list[dict[str, object]],
) -> int:
    """Attach immutable history and create an editable incremental work copy."""

    initialize_workspace(db_path)
    job_code = str(job.get("code") or "").strip()
    if not job_code:
        raise ValueError("Flow 质检任务缺少任务编号")
    raw_episodes = job.get("episodes")
    if not isinstance(raw_episodes, list):
        raise ValueError("Flow 质检任务 Episodes 必须是数组")
    episodes_by_id: dict[str, dict[str, object]] = {}
    for item in raw_episodes:
        if not isinstance(item, dict) or not item.get("episode_id"):
            raise ValueError("Flow 质检任务包含无效 Episode")
        episode_id = str(item["episode_id"])
        if episode_id in episodes_by_id:
            raise ValueError(f"Flow 质检任务包含重复 Episode：{episode_id}")
        episodes_by_id[episode_id] = item

    updated = 0
    with connect_workspace(db_path) as connection:
        for mapping in mappings:
            platform_episode_id = str(mapping.get("episode_id") or "")
            local_episode_id = str(mapping.get("local_episode_id") or "")
            platform_episode = episodes_by_id.get(platform_episode_id)
            if platform_episode is None:
                raise ValueError(
                    f"本地 Episode 映射不属于当前 Flow 任务：{platform_episode_id}"
                )
            row = connection.execute(
                """
                SELECT e.id, t.flow_job_code
                FROM episode e
                JOIN data_source ds ON ds.id = e.data_source_id
                JOIN qc_task t ON t.id = ds.task_id
                WHERE e.id = ?
                """,
                (local_episode_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"本地 Episode 不存在：{local_episode_id}")
            if str(row["flow_job_code"] or "") != job_code:
                raise ValueError("不能把上一轮质检结果写入其他本地任务")
            raw_history = platform_episode.get("review_history")
            if raw_history is None:
                raw_history = (
                    [platform_episode.get("previous_review")]
                    if platform_episode.get("previous_review") is not None
                    else []
                )
            if not isinstance(raw_history, list):
                raise ValueError("Flow 历史质检结果必须是数组")
            review_history = []
            for round_number, raw_review in enumerate(raw_history, start=1):
                safe_review = _safe_previous_review(raw_review)
                if safe_review is None:
                    continue
                safe_review["round_number"] = round_number
                review_history.append(safe_review)
            previous_review = dict(review_history[-1]) if review_history else None
            if previous_review is not None:
                previous_review.pop("round_number", None)
            connection.execute(
                "UPDATE episode SET previous_review_json = ?, review_history_count = ? WHERE id = ?",
                (
                    _json(previous_review) if previous_review is not None else None,
                    len(review_history),
                    local_episode_id,
                ),
            )
            _seed_incremental_annotations(
                connection,
                job_code=job_code,
                local_episode_id=local_episode_id,
                review_history=review_history,
            )
            updated += 1
    return updated


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


def canonical_json_sha256(value: object) -> str:
    canonical_json = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


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
    header = schema["schema"]
    label_set_id = _stable_id("ls", header["label_set_id"], header["schema_version"])
    canonical_hash = canonical_json_sha256(schema)
    with connect_workspace(db_path) as connection:
        existing = connection.execute(
            "SELECT id, raw_schema_json FROM label_set WHERE label_set_key = ? AND version = ?",
            (header["label_set_id"], header["schema_version"]),
        ).fetchone()
        if existing is not None:
            existing_schema = _loads(existing["raw_schema_json"], {})
            if canonical_json_sha256(existing_schema) != canonical_hash:
                raise ValueError(
                    "同一标签库 ID 和版本已存在不同内容，请提高版本号或更换标签库编码"
                )
            actual = str(existing["id"])
            connection.execute(
                "UPDATE label_set SET enabled = 1 WHERE id = ?",
                (actual,),
            )
        else:
            actual = label_set_id
            connection.execute(
                """
                INSERT INTO label_set(
                    id, label_set_key, name, version, language, source_format,
                    source_hash, enabled, raw_schema_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    actual,
                    header["label_set_id"],
                    header["label_set_name"],
                    header["schema_version"],
                    header.get("language", "zh-CN"),
                    preview["source_format"],
                    preview["source_hash"],
                    _json(schema),
                    _now(),
                ),
            )
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
    return {key: value for key, value in preview.items() if key != "schema"} | {
        "id": actual,
        "active": True,
    }


def install_flow_label_schema(
    db_path: str | Path, job: dict[str, object]
) -> dict[str, object]:
    """Install the exact label snapshot frozen on a Flow QC job."""

    reference_fields = (
        "label_set_id",
        "label_schema_version",
        "label_schema_hash",
        "label_schema",
    )
    provided = {
        field: job.get(field)
        for field in reference_fields
        if (
            job.get(field) is not None
            and (field == "label_schema" or job.get(field) != "")
        )
    }
    if not provided:
        return {"active": False}
    if len(provided) != len(reference_fields):
        raise ValueError("Flow 冻结标签引用不完整")

    label_set_key = provided["label_set_id"]
    version = provided["label_schema_version"]
    declared_hash = provided["label_schema_hash"]
    schema = provided["label_schema"]
    if not isinstance(label_set_key, str) or not label_set_key:
        raise ValueError("Flow 标签集 ID 无效")
    if not isinstance(version, str) or not version:
        raise ValueError("Flow 标签版本无效")
    if not isinstance(declared_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", declared_hash):
        raise ValueError("Flow 标签快照摘要必须是 64 位小写十六进制 SHA-256")
    if not isinstance(schema, dict):
        raise ValueError("Flow 标签快照必须是 JSON 对象")

    source_hash = canonical_json_sha256(schema)
    if source_hash != declared_hash:
        raise ValueError("Flow 标签快照摘要不匹配")
    errors = validate_label_schema(schema)
    if errors:
        raise ValueError("Flow 标签快照校验失败: " + "; ".join(errors))
    header = schema["schema"]
    if (
        header.get("label_set_id") != label_set_key
        or header.get("schema_version") != version
    ):
        raise ValueError("Flow 标签快照与任务引用不一致")

    with connect_workspace(db_path) as connection:
        connection.execute("BEGIN")
        _initialize_workspace(connection)
        existing = connection.execute(
            """
            SELECT id, source_hash, raw_schema_json FROM label_set
            WHERE label_set_key = ? AND version = ?
            """,
            (label_set_key, version),
        ).fetchone()
        if existing is not None:
            existing_schema = _loads(existing["raw_schema_json"])
            if canonical_json_sha256(existing_schema) != source_hash:
                raise ValueError("同一标签集版本已有不同的本地标签快照")
            actual = str(existing["id"])
            connection.execute("UPDATE label_set SET enabled = 1 WHERE id = ?", (actual,))
        else:
            actual = _stable_id("ls", label_set_key, version)
            connection.execute(
                """
                INSERT INTO label_set(
                    id, label_set_key, name, version, language, source_format,
                    source_hash, enabled, raw_schema_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'flow', ?, 1, ?, ?)
                """,
                (
                    actual,
                    label_set_key,
                    header["label_set_name"],
                    version,
                    header.get("language", "zh-CN"),
                    source_hash,
                    _json(schema),
                    _now(),
                ),
            )
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
    return {
        "id": actual,
        "label_set_id": label_set_key,
        "version": version,
        "source_format": "flow",
        "source_hash": source_hash,
        "active": True,
    }


def _active_label_schema(connection: sqlite3.Connection) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT ls.raw_schema_json FROM workspace w
        LEFT JOIN label_set ls ON ls.id = w.active_label_set_id
        LIMIT 1
        """
    ).fetchone()
    return _loads(row["raw_schema_json"], None) if row and row["raw_schema_json"] else None


def _label_schema_for_task(
    connection: sqlite3.Connection, task_id: str | None
) -> dict[str, object] | None:
    if task_id:
        row = connection.execute(
            """
            SELECT t.annotation_mode, t.annotation_schema_version, ls.raw_schema_json
            FROM qc_task t
            LEFT JOIN label_set ls ON ls.id = t.label_set_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        if row and row["annotation_mode"] == "open":
            return _open_annotation_schema(
                connection,
                task_id,
                str(row["annotation_schema_version"] or EGO_OPEN_SCHEMA_VERSION),
            )
        if row and row["raw_schema_json"]:
            return _loads(row["raw_schema_json"], None)
    return _active_label_schema(connection)


def _open_annotation_schema(
    connection: sqlite3.Connection, task_id: str, schema_version: str
) -> dict[str, object]:
    suggestions = [
        ("joint_misaligned_2d", "2D 关节未对齐", "pose_quality"),
        ("joint_jerk", "关节突跳", "pose_quality"),
        ("joint_jitter", "关节抖动", "pose_quality"),
        ("occlusion", "遮挡", "pose_quality"),
        ("foot_float", "脚部悬空或滑移", "pose_quality"),
        ("left_right_swap", "左右肢体混淆", "pose_quality"),
        ("calibration_error", "标定误差", "pose_quality"),
        ("sync_error", "时序不同步", "pose_quality"),
    ]
    seen = {item[0] for item in suggestions}
    rows = connection.execute(
        """
        SELECT a.label_slug, a.label_name, a.annotation_type, MAX(a.updated_at) AS last_used_at
        FROM annotation a
        JOIN episode e ON e.id = a.episode_id
        JOIN data_source ds ON ds.id = e.data_source_id
        WHERE ds.task_id = ? AND a.annotation_mode = 'open' AND a.deleted_at IS NULL
        GROUP BY a.label_slug, a.label_name, a.annotation_type
        ORDER BY last_used_at DESC
        LIMIT 50
        """,
        (task_id,),
    ).fetchall()
    for row in rows:
        code = str(row["label_slug"] or "")
        if not code or code in seen:
            continue
        suggestions.append((code, str(row["label_name"] or code), str(row["annotation_type"] or "other")))
        seen.add(code)
    labels = [
        {
            "code": code,
            "name": name,
            "group": annotation_type,
            "annotation_type": annotation_type,
            "description": "建议项；也可以直接输入新的自定义标签",
            "enabled": True,
            "annotation_scopes": sorted(VALID_SCOPES),
            "target_types": sorted(VALID_TARGETS),
            "default_severity": "normal",
            "default_action": "repair" if annotation_type.endswith("quality") else "keep",
            "color": "#cfef5a" if annotation_type == "action" else "#f59e68",
            "fields": [],
        }
        for code, name, annotation_type in suggestions
    ]
    return {
        "schema": {
            "schema_type": "open_annotation_schema",
            "annotation_mode": "open",
            "annotation_schema_version": schema_version,
            "schema_version": schema_version,
            "label_set_name": "Ego 开放标签",
            "language": "zh-CN",
        },
        "groups": [
            {"code": "action", "name": "动作"},
            {"code": "pose_quality", "name": "Pose 质量"},
            {"code": "camera_quality", "name": "相机质量"},
            {"code": "exception", "name": "意外与恢复"},
            {"code": "object_state", "name": "物品状态"},
            {"code": "other", "name": "其他"},
        ],
        "severity_levels": DEFAULT_SEVERITY_LEVELS,
        "actions": DEFAULT_LABEL_ACTIONS,
        "labels": labels,
    }


def _annotation_row(row: sqlite3.Row) -> dict[str, object]:
    value = dict(row)
    value["annotation_id"] = value.pop("id")
    value["attributes"] = _loads(value.pop("attributes_json"), {})
    value["label_snapshot"] = _loads(value.pop("label_snapshot_json", "{}"), {})
    return value


def _list_annotations(connection: sqlite3.Connection, episode_id: str) -> list[dict[str, object]]:
    return [
        _annotation_row(row)
        for row in connection.execute(
            "SELECT * FROM annotation WHERE episode_id = ? AND deleted_at IS NULL ORDER BY start_offset_ns, created_at",
            (episode_id,),
        )
    ]


def _deleted_annotation_lineages(
    connection: sqlite3.Connection, episode_id: str
) -> list[str]:
    lineages: set[str] = set()
    for row in connection.execute(
        "SELECT attributes_json FROM annotation WHERE episode_id = ? AND deleted_at IS NOT NULL",
        (episode_id,),
    ):
        attributes = _loads(row["attributes_json"], {})
        lineage = (
            attributes.get("_incremental_lineage_id")
            if isinstance(attributes, dict)
            else None
        )
        if isinstance(lineage, str) and lineage.strip():
            lineages.add(lineage.strip())
    return sorted(lineages)


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
        task_label = connection.execute(
            """
            SELECT t.annotation_mode, t.annotation_schema_version,
                   COALESCE(t.label_set_id, w.active_label_set_id) AS label_set_id
            FROM episode e
            JOIN data_source ds ON ds.id = e.data_source_id
            JOIN qc_task t ON t.id = ds.task_id
            CROSS JOIN workspace w
            WHERE e.id = ?
            LIMIT 1
            """,
            (episode_id,),
        ).fetchone()
        annotation_mode = str(task_label["annotation_mode"] or "library") if task_label else "library"
        schema_version = str(task_label["annotation_schema_version"] or "") if task_label else ""
        label_set = None
        label = None
        if annotation_mode == "open":
            normalized = _validate_open_annotation_payload(payload, episode)
            label_set_key = ""
            label_schema_version = ""
            label_name = normalized["label_name"]
            label_slug = normalized["label_slug"]
            annotation_type = normalized["annotation_type"]
            label_snapshot = {
                "label_name": label_name,
                "label_slug": label_slug,
                "annotation_type": annotation_type,
                "captured_at": _now(),
            }
        else:
            active_id = task_label["label_set_id"] if task_label else None
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
            label_set_key = str(label_set["label_set_key"])
            label_schema_version = str(label_set["version"])
            label_name = str(label["name"])
            label_slug = str(label["code"])
            annotation_type = "quality"
            schema_version = label_schema_version
            label_snapshot = {
                "label_name": label_name,
                "label_slug": label_slug,
                "annotation_type": annotation_type,
                "label_set_id": label_set_key,
                "label_schema_version": label_schema_version,
            }
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
                id, episode_id, label_set_key, label_schema_version, label_code,
                annotation_mode, annotation_schema_version, annotation_type,
                label_name, label_slug, label_snapshot_json, scope,
                start_offset_ns, end_offset_ns, target_type, target_key, severity, action,
                comment, attributes_json, source, status, reviewer_name, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(id) DO UPDATE SET
                label_set_key = excluded.label_set_key,
                label_schema_version = excluded.label_schema_version,
                label_code = excluded.label_code,
                annotation_mode = excluded.annotation_mode,
                annotation_schema_version = excluded.annotation_schema_version,
                annotation_type = excluded.annotation_type,
                label_name = excluded.label_name,
                label_slug = excluded.label_slug,
                label_snapshot_json = excluded.label_snapshot_json,
                scope = excluded.scope,
                start_offset_ns = excluded.start_offset_ns, end_offset_ns = excluded.end_offset_ns,
                target_type = excluded.target_type, target_key = excluded.target_key,
                severity = excluded.severity, action = excluded.action, comment = excluded.comment,
                attributes_json = excluded.attributes_json, status = excluded.status,
                reviewer_name = excluded.reviewer_name, updated_at = excluded.updated_at, deleted_at = NULL
            """,
            (
                actual_id,
                episode_id,
                label_set_key,
                label_schema_version,
                normalized["label_code"],
                annotation_mode,
                schema_version,
                annotation_type,
                label_name,
                label_slug,
                _json(label_snapshot),
                normalized["scope"],
                normalized["start_offset_ns"],
                normalized["end_offset_ns"],
                normalized["target_type"],
                normalized.get("target_key"),
                normalized.get("severity") or (label["default_severity"] if label else "normal"),
                normalized.get("action") or (label["default_action"] if label else ("repair" if annotation_type.endswith("quality") else "keep")),
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
        _mark_task_review_write(connection, episode_id, now=now)
        task_id = _task_id_for_episode(connection, episode_id)
        if task_id:
            _refresh_task_status(connection, task_id, now=now)
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


def _open_label_slug(label_name: str) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "_", label_name.lower()).strip("_")[:48]
    if ascii_slug and re.match(r"^[a-z]", ascii_slug):
        return ascii_slug
    return f"custom_{hashlib.sha256(label_name.encode('utf-8')).hexdigest()[:12]}"


def _validate_open_annotation_payload(
    payload: dict[str, object], episode: sqlite3.Row
) -> dict[str, object]:
    value = dict(payload)
    label_name = str(value.get("label_name") or "").strip()
    if not label_name:
        raise ValueError("开放标注必须填写标签名称")
    if len(label_name) > 120:
        raise ValueError("标签名称不能超过 120 个字符")
    annotation_type = str(value.get("annotation_type") or "action").strip()
    if annotation_type not in OPEN_ANNOTATION_TYPES:
        raise ValueError(f"不支持的开放标注类型: {annotation_type}")
    scope = str(value.get("scope") or "")
    target = str(value.get("target_type") or "")
    if scope not in VALID_SCOPES:
        raise ValueError(f"不支持的标注范围: {scope}")
    if target not in VALID_TARGETS:
        raise ValueError(f"不支持的标注对象: {target}")
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
    label_slug = str(value.get("label_slug") or "").strip()
    if not LABEL_CODE_RE.fullmatch(label_slug):
        label_slug = _open_label_slug(label_name)
    value.update(
        {
            "label_code": label_slug,
            "label_name": label_name,
            "label_slug": label_slug,
            "annotation_type": annotation_type,
            "scope": scope,
            "target_type": target,
            "start_offset_ns": start,
            "end_offset_ns": end,
            "attributes": attributes,
        }
    )
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
        _mark_task_review_write(connection, str(row["episode_id"]), now=now)
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
            UPDATE annotation SET label_set_key=?, label_schema_version=?, label_code=?,
                annotation_mode=?, annotation_schema_version=?, annotation_type=?, label_name=?, label_slug=?,
                label_snapshot_json=?, scope=?, start_offset_ns=?, end_offset_ns=?, target_type=?, target_key=?,
                severity=?, action=?, comment=?, attributes_json=?, source=?, status=?, reviewer_name=?, updated_at=?, deleted_at=? WHERE id=?
            """,
            (
                snapshot.get("label_set_key", ""), snapshot.get("label_schema_version", ""), snapshot["label_code"],
                snapshot.get("annotation_mode", "library"), snapshot.get("annotation_schema_version", ""),
                snapshot.get("annotation_type", "quality"), snapshot.get("label_name", ""),
                snapshot.get("label_slug", snapshot["label_code"]), _json(snapshot.get("label_snapshot", {})),
                snapshot["scope"], snapshot["start_offset_ns"], snapshot["end_offset_ns"],
                snapshot["target_type"], snapshot.get("target_key"), snapshot.get("severity"), snapshot.get("action"),
                snapshot.get("comment"), _json(snapshot.get("attributes", {})), snapshot.get("source", "manual"),
                snapshot.get("status", "confirmed"), snapshot.get("reviewer_name", ""), _now(), snapshot.get("deleted_at"), annotation_id,
            ),
        )
    if episode_id:
        _refresh_annotation_count(connection, str(episode_id))
        _mark_task_review_write(connection, str(episode_id))


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
        now = _now()
        status = review_status if review_status is not None else row["review_status"]
        decision = quality_decision if quality_decision is not None else row["quality_decision"]
        reviewer = reviewer_name if reviewer_name is not None else row["reviewer_name"]
        playhead = int(last_playhead_ns if last_playhead_ns is not None else row["last_playhead_ns"])
        duration = int(row["duration_ns"] or 0)
        playhead = max(0, min(playhead, duration))
        review_write = review_status is not None or quality_decision is not None
        reviewed_at = (
            now
            if review_write and status in {"completed", "reviewed"}
            else row["reviewed_at"]
        )
        connection.execute(
            "UPDATE episode SET review_status=?, quality_decision=?, reviewer_name=?, last_playhead_ns=?, reviewed_at=?, updated_at=? WHERE id=?",
            (status, decision, reviewer, playhead, reviewed_at, now, episode_id),
        )
        if review_write:
            _mark_task_review_write(connection, episode_id, now=now)
        task = connection.execute(
            "SELECT ds.task_id FROM data_source ds WHERE ds.id = ?",
            (row["data_source_id"],),
        ).fetchone()
        if task and task["task_id"]:
            _refresh_task_status(connection, str(task["task_id"]), now=now)
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
                       ds.root_path AS source_root, COALESCE(NULLIF(a.label_name, ''), ld.name) AS resolved_label_name
                FROM annotation a
                JOIN episode e ON e.id = a.episode_id
                JOIN data_source ds ON ds.id = e.data_source_id
                LEFT JOIN label_set ls ON ls.label_set_key = a.label_set_key AND ls.version = a.label_schema_version
                LEFT JOIN label_definition ld ON ld.label_set_id = ls.id AND ld.code = a.label_code
                WHERE a.deleted_at IS NULL AND a.episode_id IN ({','.join('?' for _ in selected_ids)})
                ORDER BY e.relative_path, a.start_offset_ns, a.created_at
            """
            annotations = [_export_annotation_row(row) for row in connection.execute(query, selected_ids)]
        schema = _label_schema_for_task(connection, task_id) or {}

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
        "annotation_mode": row["annotation_mode"],
        "annotation_schema_version": row["annotation_schema_version"],
        "annotation_type": row["annotation_type"],
        "label_code": row["label_code"],
        "label_name": row["resolved_label_name"] or row["label_code"],
        "label_slug": row["label_slug"] or row["label_code"],
        "label_snapshot_json": row["label_snapshot_json"],
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
        "annotation_id", "annotation_mode", "annotation_schema_version", "annotation_type",
        "label_set_id", "label_schema_version", "label_code", "label_name", "label_slug", "label_snapshot_json",
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
        "annotation_id", "annotation_mode", "annotation_schema_version", "annotation_type",
        "label_set_id", "label_schema_version", "label_code", "label_name", "label_slug", "label_snapshot_json",
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
