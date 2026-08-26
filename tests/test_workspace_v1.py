from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import struct
from pathlib import Path

import pytest
import yaml
from mcap.writer import CompressionType, Writer
from PIL import Image

from episode_qc.playback import (
    G1_29_JOINT_NAMES,
    G1_MUJOCO_TO_ISAACLAB_INDICES,
    prepare_episode_cache,
    read_cached_camera_frame,
    read_cached_motion_frame,
    read_cached_robot_action_frame,
)
from episode_qc.workspace import (
    activate_label_set,
    backup_workspace_database,
    canonical_json_sha256,
    clear_local_task_history,
    connect_workspace,
    delete_label_set,
    delete_annotation,
    episode_detail,
    export_workspace,
    import_label_schema,
    install_flow_label_schema,
    initialize_workspace,
    list_label_sets,
    list_qc_tasks,
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


FLOW_SCHEMA = {
    "schema": {
        "schema_type": "annotation_label_schema",
        "schema_version": "1.0.0",
        "label_set_id": "task-quality",
        "label_set_name": "任务质量标签",
        "language": "zh-CN",
    },
    "severity_levels": [{"code": "normal", "name": "一般", "order": 1}],
    "actions": [{"code": "keep", "name": "保留"}],
    "groups": [{"code": "motion", "name": "动作", "order": 1}],
    "labels": [
        {
            "code": "body_sway",
            "name": "身体摇晃",
            "group": "motion",
            "enabled": True,
            "annotation_scopes": ["episode"],
            "target_types": ["global"],
            "description": "",
            "default_severity": "normal",
            "default_action": "keep",
            "shortcut": "W",
            "color": "#3377AA",
            "applicable_profiles": [],
            "fields": [],
        }
    ],
}


def test_workspace_connection_waits_for_long_running_transient_writer(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    initialize_workspace(db_path)

    connection = connect_workspace(db_path)
    try:
        busy_timeout_ms = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        connection.close()

    assert busy_timeout_ms == 30_000


def test_workspace_backup_preserves_completed_qc_results(tmp_path: Path):
    source_root = tmp_path / "dataset"
    _write_sample_episode(source_root / "episode_000001")
    db_path = tmp_path / "workspace.db"
    scanned = scan_data_source(db_path, source_root)
    episode_id = str(scanned["episodes"][0]["id"])
    update_episode_review(
        db_path,
        episode_id,
        review_status="completed",
        quality_decision="pass",
        reviewer_name="质检员甲",
    )

    backup_path = backup_workspace_database(
        db_path,
        tmp_path / "backups",
        reason="cache-recovery-QCJ-00006",
    )
    (source_root / "episode_000001" / "episode.mcap").touch()
    scan_data_source(db_path, source_root)
    rescanned = workspace_state(db_path)
    assert rescanned["tasks"][0]["completed_count"] == 1
    assert rescanned["episodes"][0]["quality_decision"] == "pass"
    update_episode_review(db_path, episode_id, quality_decision="recollect")

    snapshot = workspace_state(backup_path)
    assert backup_path.parent == tmp_path / "backups"
    assert "cache-recovery-QCJ-00006" in backup_path.name
    assert snapshot["tasks"][0]["completed_count"] == 1
    assert snapshot["episodes"][0]["review_status"] == "completed"
    assert snapshot["episodes"][0]["quality_decision"] == "pass"
    assert snapshot["episodes"][0]["reviewer_name"] == "质检员甲"


def test_flow_label_schema_installs_exact_snapshot_and_accepts_its_label(tmp_path: Path):
    root = tmp_path / "dataset"
    _write_sample_episode(root / "episode_000001")
    db_path = tmp_path / "workspace.db"
    scanned = scan_data_source(db_path, root)
    flow_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema": FLOW_SCHEMA,
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }

    installed = install_flow_label_schema(db_path, flow_job)
    rebound = scan_data_source(
        db_path,
        root,
        label_set_id=str(installed["id"]),
    )
    episode_id = rebound["episodes"][0]["id"]
    annotation = save_annotation(
        db_path,
        {
            "episode_id": episode_id,
            "label_code": "body_sway",
            "scope": "episode",
            "target_type": "global",
        },
    )

    assert installed["label_set_id"] == "task-quality"
    assert installed["active"] is True
    assert annotation["label_code"] == "body_sway"


def test_flow_label_schema_rejects_different_local_schema_at_same_version(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    local_schema = json.loads(json.dumps(FLOW_SCHEMA))
    local_schema["labels"][0]["code"] = "local_sway"
    local_path = tmp_path / "local-labels.yaml"
    local_path.write_text(yaml.safe_dump(local_schema, allow_unicode=True), encoding="utf-8")
    import_label_schema(db_path, local_path)
    flow_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema": FLOW_SCHEMA,
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }

    with pytest.raises(ValueError, match="同一标签集版本"):
        install_flow_label_schema(db_path, flow_job)

    assert workspace_state(db_path)["label_schema"]["labels"][0]["code"] == "local_sway"


def test_flow_label_schema_accepts_matching_content_with_different_stored_source_hash(
    tmp_path: Path,
):
    db_path = tmp_path / "workspace.db"
    local_path = tmp_path / "local-labels.yaml"
    local_path.write_text(yaml.safe_dump(FLOW_SCHEMA, allow_unicode=True), encoding="utf-8")
    import_label_schema(db_path, local_path)
    before = _label_set_storage_state(db_path)
    flow_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema": FLOW_SCHEMA,
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }

    assert before["source_hash"] != flow_job["label_schema_hash"]
    assert canonical_json_sha256(json.loads(before["raw_schema_json"])) == flow_job[
        "label_schema_hash"
    ]
    installed = install_flow_label_schema(db_path, flow_job)

    assert installed["id"] == before["active_id"]
    assert _label_set_storage_state(db_path)["active_id"] == before["active_id"]


def test_flow_label_schema_conflict_rolls_back_workspace_initialization(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    local_path = tmp_path / "local-labels.yaml"
    local_schema = json.loads(json.dumps(FLOW_SCHEMA))
    local_schema["labels"][0]["code"] = "local_sway"
    local_path.write_text(yaml.safe_dump(local_schema, allow_unicode=True), encoding="utf-8")
    import_label_schema(db_path, local_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE qc_task DROP COLUMN source_type")
        connection.execute("UPDATE workspace SET schema_version = 0")
    before = _label_set_storage_state(db_path)
    before_task_columns = _table_columns(db_path, "qc_task")
    flow_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema": FLOW_SCHEMA,
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }

    assert before["workspace"][5] == 0
    assert "source_type" not in before_task_columns
    with pytest.raises(ValueError, match="同一标签集版本"):
        install_flow_label_schema(db_path, flow_job)

    assert _label_set_storage_state(db_path) == before
    assert _table_columns(db_path, "qc_task") == before_task_columns


@pytest.mark.parametrize(
    "missing_field",
    ("label_set_id", "label_schema_version", "label_schema_hash", "label_schema"),
)
def test_flow_label_schema_rejects_each_partial_reference_without_creating_workspace(
    tmp_path: Path, missing_field: str
):
    db_path = tmp_path / "workspace.db"
    flow_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema": FLOW_SCHEMA,
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }
    del flow_job[missing_field]

    with pytest.raises(ValueError, match="不完整"):
        install_flow_label_schema(db_path, flow_job)

    assert not db_path.exists()


def test_flow_label_schema_rejects_bad_declared_hash_without_creating_workspace(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    flow_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema": FLOW_SCHEMA,
        "label_schema_hash": "0" * 64,
    }

    with pytest.raises(ValueError, match="摘要不匹配"):
        install_flow_label_schema(db_path, flow_job)

    assert not db_path.exists()


def test_flow_label_schema_ignores_legacy_job_without_creating_workspace(tmp_path: Path):
    db_path = tmp_path / "workspace.db"

    assert install_flow_label_schema(db_path, {}) == {"active": False}

    assert not db_path.exists()


def test_flow_label_schema_treats_empty_flow_reference_as_unlabeled(tmp_path: Path):
    """Catches Flow's empty hash being mistaken for a partial label snapshot."""
    db_path = tmp_path / "workspace.db"
    flow_job = {
        "label_set_id": None,
        "label_schema_version": None,
        "label_schema_hash": "",
        "label_schema": None,
    }

    assert install_flow_label_schema(db_path, flow_job) == {"active": False}
    assert not db_path.exists()


def test_flow_label_schema_content_conflict_leaves_local_storage_unchanged(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    local_schema = json.loads(json.dumps(FLOW_SCHEMA))
    local_schema["labels"][0]["code"] = "local_sway"
    local_path = tmp_path / "local-labels.yaml"
    local_path.write_text(yaml.safe_dump(local_schema, allow_unicode=True), encoding="utf-8")
    import_label_schema(db_path, local_path)
    before = _label_set_storage_state(db_path)
    flow_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema": FLOW_SCHEMA,
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }

    with pytest.raises(ValueError, match="同一标签集版本"):
        install_flow_label_schema(db_path, flow_job)

    assert _label_set_storage_state(db_path) == before


def test_local_import_rejects_different_content_at_same_id_and_version(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    original_path = tmp_path / "labels-v1.yaml"
    original_path.write_text(yaml.safe_dump(FLOW_SCHEMA, allow_unicode=True), encoding="utf-8")
    import_label_schema(db_path, original_path)
    before = _label_set_storage_state(db_path)

    conflicting = json.loads(json.dumps(FLOW_SCHEMA))
    conflicting["labels"][0]["name"] = "被原地修改的名称"
    conflict_path = tmp_path / "labels-v1-conflict.yaml"
    conflict_path.write_text(yaml.safe_dump(conflicting, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="提高版本号"):
        import_label_schema(db_path, conflict_path)

    assert _label_set_storage_state(db_path) == before


def test_flow_tasks_keep_their_bound_schema_when_local_active_schema_changes(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    local_root = tmp_path / "local"
    _write_sample_episode(first_root / "episode_000001")
    _write_sample_episode(second_root / "episode_000001")
    _write_sample_episode(local_root / "episode_000001")

    first_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "1.0.0",
        "label_schema": FLOW_SCHEMA,
        "label_schema_hash": canonical_json_sha256(FLOW_SCHEMA),
    }
    first_label_set = install_flow_label_schema(db_path, first_job)
    first = scan_data_source(
        db_path,
        first_root,
        task_code="QCJ-FIRST",
        origin="flow",
        flow_job_code="QCJ-FIRST",
        label_set_id=str(first_label_set["id"]),
    )

    second_schema = json.loads(json.dumps(FLOW_SCHEMA))
    second_schema["schema"]["label_set_id"] = "task-quality-second"
    second_schema["labels"][0]["code"] = "camera_shake"
    second_schema["labels"][0]["name"] = "相机抖动"
    second_job = {
        "label_set_id": "task-quality-second",
        "label_schema_version": "1.0.0",
        "label_schema": second_schema,
        "label_schema_hash": canonical_json_sha256(second_schema),
    }
    second_label_set = install_flow_label_schema(db_path, second_job)
    second = scan_data_source(
        db_path,
        second_root,
        task_code="QCJ-SECOND",
        origin="flow",
        flow_job_code="QCJ-SECOND",
        label_set_id=str(second_label_set["id"]),
    )

    local_schema = json.loads(json.dumps(FLOW_SCHEMA))
    local_schema["schema"]["label_set_id"] = "local-quality"
    local_schema["labels"][0]["code"] = "local_only"
    local_schema["labels"][0]["name"] = "本地标签"
    local_path = tmp_path / "local.yaml"
    local_path.write_text(yaml.safe_dump(local_schema, allow_unicode=True), encoding="utf-8")
    import_label_schema(db_path, local_path)
    local = scan_data_source(db_path, local_root, task_code="LOCAL-TASK")

    assert workspace_state(db_path, task_id=first["task_id"])["label_schema"] == FLOW_SCHEMA
    assert workspace_state(db_path, task_id=second["task_id"])["label_schema"] == second_schema
    assert workspace_state(db_path, task_id=local["task_id"])["label_schema"] == local_schema
    assert episode_detail(db_path, first["episodes"][0]["id"])["label_schema"] == FLOW_SCHEMA
    assert episode_detail(db_path, second["episodes"][0]["id"])["label_schema"] == second_schema


def test_flow_previous_review_is_stored_read_only_and_survives_rescan(tmp_path: Path):
    source_root = tmp_path / "cached-flow"
    _write_sample_episode(source_root / "episodes" / "episode_000001")
    db_path = tmp_path / "workspace.db"
    scanned = scan_data_source(
        db_path,
        source_root,
        task_code="QCJ-RECHECK-001",
        origin="flow",
        flow_job_code="QCJ-RECHECK-001",
    )
    local_episode_id = str(scanned["episodes"][0]["id"])
    previous_review = {
        "episode_review_result_id": 91,
        "review_attempt_id": 72,
        "job_code": "QCJ-INITIAL-001",
        "attempt_version": 1,
        "reviewer_name": "历史质检员",
        "decision": "pass_with_labels",
        "quality_grade": "good",
        "source": {
            "source_type": "feishu_history_qc",
            "source_file_name": "历史质检结果.json",
            "annotation_record_id": "rec-history",
        },
        "annotations": [
            {
                "id": 101,
                "label_code": "legacy_body_sway",
                "label_name": "历史躯干摆动",
                "label_color": "#8844EE",
                "scope": "time_range",
                "start_offset_ns": 1_000_000_000,
                "end_offset_ns": 2_000_000_000,
                "severity": "normal",
                "comment": "上一轮备注",
            }
        ],
    }
    job = {
        "code": "QCJ-RECHECK-001",
        "episodes": [
            {
                "episode_id": "AST-HISTORY-EP0001",
                "relative_path": "episodes/episode_000001",
                "previous_review": previous_review,
            }
        ],
    }

    updated = sync_flow_previous_reviews(
        db_path,
        job,
        [
            {
                "episode_id": "AST-HISTORY-EP0001",
                "local_episode_id": local_episode_id,
                "relative_path": "episodes/episode_000001",
            }
        ],
    )

    assert updated == 1
    detail = episode_detail(db_path, local_episode_id)
    assert detail["episode"]["previous_review"] == previous_review
    assert detail["annotations"] == []

    scan_data_source(
        db_path,
        source_root,
        task_code="QCJ-RECHECK-001",
        origin="flow",
        flow_job_code="QCJ-RECHECK-001",
    )
    assert episode_detail(db_path, local_episode_id)["episode"]["previous_review"] == previous_review


def test_flow_incremental_history_is_editable_and_deleted_labels_do_not_return(
    tmp_path: Path,
):
    source_root = tmp_path / "cached-flow-incremental"
    _write_sample_episode(source_root / "episodes" / "episode_000001")
    db_path = tmp_path / "workspace.db"
    current_schema = json.loads(json.dumps(FLOW_SCHEMA))
    current_schema["schema"]["schema_version"] = "3.0.0"
    current_schema["labels"].append(
        {
            **current_schema["labels"][0],
            "code": "camera_shake",
            "name": "相机抖动",
            "shortcut": "C",
        }
    )
    flow_job = {
        "label_set_id": "task-quality",
        "label_schema_version": "3.0.0",
        "label_schema": current_schema,
        "label_schema_hash": canonical_json_sha256(current_schema),
    }
    installed = install_flow_label_schema(db_path, flow_job)
    scanned = scan_data_source(
        db_path,
        source_root,
        task_code="QCJ-V3",
        origin="flow",
        flow_job_code="QCJ-V3",
        label_set_id=str(installed["id"]),
    )
    local_episode_id = str(scanned["episodes"][0]["id"])
    first_lineage = "QCJ-V1:ann-1"
    histories = [
        {
            "job_code": "QCJ-V1",
            "review_attempt_id": 1,
            "episode_review_result_id": 11,
            "label_set": {"schema_version": "1.0.0"},
            "annotations": [
                {
                    "id": "ann-1",
                    "label_code": "body_sway",
                    "scope": "episode",
                    "target_type": "global",
                    "severity": "normal",
                    "action": "keep",
                    "comment": "第一轮",
                }
            ],
        },
        {
            "job_code": "QCJ-V2",
            "review_attempt_id": 2,
            "episode_review_result_id": 22,
            "label_set": {"schema_version": "2.0.0"},
            "annotations": [
                {
                    "id": "ann-2",
                    "label_code": "camera_shake",
                    "scope": "episode",
                    "target_type": "global",
                    "severity": "normal",
                    "action": "keep",
                    "comment": "第二轮新增",
                }
            ],
        },
    ]
    job = {
        "code": "QCJ-V3",
        "episodes": [
            {
                "episode_id": "AST-INCREMENTAL-EP0001",
                "relative_path": "episodes/episode_000001",
                "review_history": histories,
                "previous_review": histories[-1],
            }
        ],
    }
    mappings = [
        {
            "episode_id": "AST-INCREMENTAL-EP0001",
            "local_episode_id": local_episode_id,
            "relative_path": "episodes/episode_000001",
        }
    ]

    assert sync_flow_previous_reviews(db_path, job, mappings) == 1
    detail = episode_detail(db_path, local_episode_id)
    assert [item["label_code"] for item in detail["annotations"]] == [
        "body_sway",
        "camera_shake",
    ]
    inherited_body, inherited_camera = detail["annotations"]
    assert inherited_body["attributes"]["_incremental_lineage_id"] == first_lineage
    assert inherited_body["attributes"]["_incremental_source"]["round_number"] == 1
    assert inherited_body["attributes"]["_incremental_source"]["origin_round_number"] == 1
    assert inherited_camera["attributes"]["_incremental_source"]["schema_version"] == "2.0.0"
    assert inherited_camera["attributes"]["_incremental_source"]["round_number"] == 2
    assert detail["episode"]["review_history_count"] == 2
    episode_summary = workspace_state(db_path, task_id=scanned["task_id"])["episodes"][0]
    assert episode_summary["incremental_added_count"] == 0
    assert episode_summary["incremental_modified_count"] == 0
    assert episode_summary["incremental_removed_count"] == 0
    assert episode_summary["incremental_preserved_count"] == 2

    delete_annotation(db_path, inherited_body["annotation_id"])
    saved_camera = save_annotation(
        db_path,
        {
            "episode_id": local_episode_id,
            "label_code": inherited_camera["label_code"],
            "scope": inherited_camera["scope"],
            "start_offset_ns": inherited_camera["start_offset_ns"],
            "end_offset_ns": inherited_camera["end_offset_ns"],
            "target_type": inherited_camera["target_type"],
            "target_key": inherited_camera["target_key"],
            "severity": inherited_camera["severity"],
            "action": inherited_camera["action"],
            "comment": "第三轮已修改",
            "attributes": inherited_camera["attributes"],
        },
        annotation_id=inherited_camera["annotation_id"],
    )
    assert saved_camera["comment"] == "第三轮已修改"

    # A later Flow refresh is idempotent: local edits and deletion tombstones win.
    assert sync_flow_previous_reviews(db_path, job, mappings) == 1
    refreshed = episode_detail(db_path, local_episode_id)
    assert len(refreshed["annotations"]) == 1
    assert refreshed["annotations"][0]["comment"] == "第三轮已修改"
    assert refreshed["deleted_annotation_lineages"] == [first_lineage]
    episode_summary = workspace_state(db_path, task_id=scanned["task_id"])["episodes"][0]
    assert episode_summary["incremental_added_count"] == 0
    assert episode_summary["incremental_modified_count"] == 1
    assert episode_summary["incremental_removed_count"] == 1
    assert episode_summary["incremental_preserved_count"] == 0


def test_v1_import_playback_annotation_and_export_round_trip(tmp_path: Path):
    source_root = tmp_path / "含 空格的数据"
    mcap_path = _write_sample_episode(source_root / "episode_000001")
    source_before = (mcap_path.stat().st_size, mcap_path.stat().st_mtime_ns)
    db_path = tmp_path / "workspace" / "workspace.db"
    schema_path = _write_label_schema(tmp_path / "labels.yaml")

    workspace = initialize_workspace(db_path, reviewer_name="测试员")
    result = scan_data_source(db_path, source_root)

    assert workspace["schema_version"] == 5
    assert result["discovered"] == 1
    assert result["ready"] == 1
    episode_id = result["episodes"][0]["id"]
    detail = episode_detail(db_path, episode_id)
    assert detail["episode"]["camera_count"] == 1
    assert detail["episode"]["mocap_available"] is True
    assert [stream["message_count"] for stream in detail["streams"] if stream["stream_type"] == "camera"] == [3, 0]
    update_workspace_settings(db_path, last_episode_id=episode_id)
    assert workspace_state(db_path)["workspace"]["settings"]["last_episode_id"] == episode_id

    preview = preview_label_schema(db_path, schema_path)
    assert preview["valid"] is True
    assert preview["added"] == ["camera_blur", "mocap_joint_jitter"]
    imported = import_label_schema(db_path, schema_path)
    assert imported["active"] is True
    assert import_label_schema(db_path, schema_path)["unchanged"] == ["camera_blur", "mocap_joint_jitter"]

    manifest = prepare_episode_cache(db_path, episode_id, tmp_path / "cache")
    assert len(manifest["cameras"]) == 1
    assert manifest["motion"]["available"] is True
    assert manifest["motion"]["joint_names"] == ["Hips", "Head"]
    assert manifest["cache_version"] == 6
    assert manifest["motion"]["frame_encoding"] == "episode-qc-motion-f32-le-v1"
    assert manifest["robot_actions"]["default_source"] == "policy"
    assert {item["key"] for item in manifest["robot_actions"]["sources"] if item["available"]} == {
        "policy", "policy_target", "policy_command", "soma",
    }
    frame = read_cached_camera_frame(manifest["manifest_path"], manifest["cameras"][0]["stream_id"], 1_050_000_000)
    assert frame["frame_index"] == 1
    assert frame["jpeg"].startswith(b"\xff\xd8")
    motion = read_cached_motion_frame(manifest["manifest_path"], 1_050_000_000)
    assert motion["positions"][1] == pytest.approx([0.1, 0.0, 1.0])
    assert motion["parent_indices"] == [-1, -1]
    policy = read_cached_robot_action_frame(manifest["manifest_path"], "policy", 1_050_000_000)
    policy_target = read_cached_robot_action_frame(manifest["manifest_path"], "policy_target", 1_050_000_000)
    policy_command = read_cached_robot_action_frame(manifest["manifest_path"], "policy_command", 1_050_000_000)
    soma = read_cached_robot_action_frame(manifest["manifest_path"], "soma", 1_050_000_000)
    assert policy["joint_names"] == G1_29_JOINT_NAMES
    assert policy["joint_positions"] == pytest.approx([1 + joint / 100 for joint in range(29)])
    assert "root_position" not in policy
    assert policy["root_quaternion_wxyz"] == pytest.approx([1.0, 0.0, 0.0, 0.0])
    assert policy_target["joint_positions"] == pytest.approx([
        151 + isaaclab_index / 100 for isaaclab_index in G1_MUJOCO_TO_ISAACLAB_INDICES
    ])
    assert policy_target["root_position"] == pytest.approx([1.1, 1.2, 1.3])
    assert policy_target["root_quaternion_wxyz"] == pytest.approx([0.5, 0.5, -0.5, -0.5])
    assert policy_command["joint_positions"] == pytest.approx([51 + joint / 100 for joint in range(29)])
    assert soma["joint_positions"] == pytest.approx([101 + joint / 100 for joint in range(29)])
    assert soma["root_position"] == pytest.approx([0.1, 0.2, 0.3])

    annotation = save_annotation(
        db_path,
        {
            "episode_id": episode_id,
            "label_code": "camera_blur",
            "scope": "time_range",
            "start_offset_ns": 500_000_000,
            "end_offset_ns": 1_500_000_000,
            "target_type": "camera",
            "target_key": "/camera/ego_head/image/jpeg",
            "severity": "normal",
            "action": "trim",
            "comment": "测试画面模糊",
            "attributes": {},
        },
        session_id="test",
    )
    started_task = list_qc_tasks(db_path)[0]
    assert started_task["review_started_at"]
    assert started_task["review_completed_at"] is None
    with pytest.raises(WorkspaceConflictError, match="另一个页面"):
        save_annotation(
            db_path,
            {**annotation, "comment": "过期页面修改"},
            annotation_id=annotation["annotation_id"],
            session_id="browser-tab",
            expected_updated_at="stale-version",
        )
    assert annotation["reviewer_name"] == "测试员"
    assert episode_detail(db_path, episode_id)["episode"]["annotation_count"] == 1
    assert undo_annotation_change(db_path, session_id="test")["operation"] == "undo"
    assert episode_detail(db_path, episode_id)["annotations"] == []
    assert redo_annotation_change(db_path, session_id="test")["operation"] == "redo"
    assert len(episode_detail(db_path, episode_id)["annotations"]) == 1

    update_episode_review(db_path, episode_id, review_status="completed", quality_decision="pass_with_labels", last_playhead_ns=1_100_000_000)
    completed_task = list_qc_tasks(db_path)[0]
    assert completed_task["review_started_at"] == started_task["review_started_at"]
    assert completed_task["review_completed_at"]
    assert completed_task["review_completed_at"] >= completed_task["review_started_at"]
    completed_reviewed_at = episode_detail(db_path, episode_id)["episode"]["reviewed_at"]
    update_episode_review(db_path, episode_id, last_playhead_ns=1_200_000_000)
    after_playhead_task = list_qc_tasks(db_path)[0]
    assert episode_detail(db_path, episode_id)["episode"]["reviewed_at"] == completed_reviewed_at
    assert after_playhead_task["review_started_at"] == completed_task["review_started_at"]
    assert after_playhead_task["review_completed_at"] == completed_task["review_completed_at"]
    json_export_root = tmp_path / "exports-json"
    exported = export_workspace(
        db_path,
        json_export_root,
        completed_only=True,
        export_format="json",
    )
    output = Path(exported["output_file"])
    assert exported["episode_count"] == 1
    assert exported["annotation_count"] == 1
    assert exported["task_count"] == 1
    assert exported["output_files"] == [str(output)]
    assert exported["format"] == "json"
    assert output.name == "含 空格的数据_标注结果.json"
    assert exported["source_directories"] == [str(source_root.resolve())]
    assert [path.name for path in json_export_root.iterdir()] == [output.name]
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["task_name"] == "含 空格的数据"
    assert document["episode_count"] == 1
    assert len(document["episodes"]) == 1
    assert len(document["annotations"]) == 1
    assert document["annotations"][0]["episode_id"] == episode_id
    assert document["annotations"][0]["absolute_start_time_ns"] == 10_500_000_000
    assert document["annotations"][0]["label_schema_version"] == "1.0.0"

    csv_export_root = tmp_path / "exports-csv"
    csv_exported = export_workspace(db_path, csv_export_root, export_format="csv")
    csv_output = Path(csv_exported["output_file"])
    assert csv_output.name == "含 空格的数据_标注结果.csv"
    assert [path.name for path in csv_export_root.iterdir()] == [csv_output.name]
    with csv_output.open(encoding="utf-8-sig", newline="") as source:
        csv_rows = list(csv.DictReader(source))
    assert len(csv_rows) == 1
    assert csv_rows[0]["task_name"] == "含 空格的数据"
    assert csv_rows[0]["episode_id"] == episode_id
    assert csv_rows[0]["annotation_id"] == annotation["annotation_id"]
    assert csv_rows[0]["quality_decision"] == "pass_with_labels"
    assert csv_rows[0]["label_schema_version"] == "1.0.0"

    with pytest.raises(ValueError, match="csv 或 json"):
        export_workspace(db_path, tmp_path / "invalid-export", export_format="jsonl")
    assert (mcap_path.stat().st_size, mcap_path.stat().st_mtime_ns) == source_before

    delete_annotation(db_path, annotation["annotation_id"])
    assert workspace_state(db_path)["episodes"][0]["annotation_count"] == 0


def test_export_writes_exactly_one_result_file_per_data_task(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    first_root = tmp_path / "任务甲"
    second_root = tmp_path / "任务乙"
    _write_sample_episode(first_root / "episode_000001")
    _write_sample_episode(second_root / "episode_000001")
    scan_data_source(db_path, first_root)
    scan_data_source(db_path, second_root)

    export_root = tmp_path / "exports"
    result = export_workspace(db_path, export_root, export_format="json")

    assert result["task_count"] == 2
    assert result["episode_count"] == 2
    assert {Path(path).name for path in result["output_files"]} == {
        "任务甲_标注结果.json",
        "任务乙_标注结果.json",
    }
    assert {path.name for path in export_root.iterdir()} == {
        "任务甲_标注结果.json",
        "任务乙_标注结果.json",
    }
    for task in result["tasks"]:
        document = json.loads(Path(task["output_file"]).read_text(encoding="utf-8"))
        assert document["task_name"] == task["task_name"]
        assert document["episode_count"] == 1
        assert len(document["episodes"]) == 1
        assert {item["source_root"] for item in document["episodes"]} == set(document["source_directories"])


def test_v1_import_restores_existing_annotations_json(tmp_path: Path):
    source_root = tmp_path / "含 结果文件"
    _write_sample_episode(source_root / "episode_000001")
    db_path = tmp_path / "workspace.db"
    schema_path = _write_label_schema(tmp_path / "labels.yaml")
    import_label_schema(db_path, schema_path)

    task = scan_data_source(db_path, source_root)
    episode_id = task["episodes"][0]["id"]
    annotation = save_annotation(
        db_path,
        {
            "episode_id": episode_id,
            "label_code": "camera_blur",
            "scope": "time_range",
            "start_offset_ns": 500_000_000,
            "end_offset_ns": 1_500_000_000,
            "target_type": "camera",
            "target_key": "/camera/ego_head/image/jpeg",
            "comment": "导入前标注",
            "attributes": {"camera_state": "blurred"},
            "reviewer_name": "质检员甲",
        },
    )
    update_episode_review(
        db_path,
        episode_id,
        review_status="completed",
        quality_decision="pass_with_labels",
        reviewer_name="质检员甲",
    )
    exported = export_workspace(db_path, tmp_path / "exports", export_format="json")
    exported_file = Path(exported["output_file"])
    (source_root / exported_file.name).write_text(exported_file.read_text(encoding="utf-8"), encoding="utf-8")

    restore_db = tmp_path / "restore.db"
    import_label_schema(restore_db, schema_path)
    restored = scan_data_source(restore_db, source_root)

    assert restored["restored_annotations"] == 1
    assert restored["restored_episode_states"] == 1
    assert restored["import_warnings"] == []
    restored_episode_id = restored["episodes"][0]["id"]
    detail = episode_detail(restore_db, restored_episode_id)
    assert detail["episode"]["annotation_count"] == 1
    assert detail["episode"]["review_status"] == "completed"
    assert detail["episode"]["quality_decision"] == "pass_with_labels"
    assert detail["annotations"][0]["label_code"] == annotation["label_code"]
    assert detail["annotations"][0]["annotation_id"] == annotation["annotation_id"]
    assert detail["annotations"][0]["attributes"] == {"camera_state": "blurred"}
    assert detail["annotations"][0]["reviewer_name"] == "质检员甲"


def test_v1_import_restores_completed_episode_states_without_annotations(tmp_path: Path):
    source_root = tmp_path / "已完成无问题标注"
    _write_sample_episode(source_root / "episode_000001")
    _write_sample_episode(source_root / "episode_000002")

    author_db = tmp_path / "author.db"
    authored = scan_data_source(author_db, source_root)
    for index, episode in enumerate(authored["episodes"], start=1):
        update_episode_review(
            author_db,
            str(episode["id"]),
            review_status="completed",
            quality_decision="pass",
            reviewer_name=f"质检员{index}",
        )
    exported = export_workspace(author_db, tmp_path / "exports", export_format="json")
    exported_file = Path(exported["output_file"])
    shutil.copy2(exported_file, source_root / exported_file.name)

    restore_db = tmp_path / "restore.db"
    restored = scan_data_source(restore_db, source_root)

    assert restored["restored_annotations"] == 0
    assert restored["restored_episode_states"] == 2
    assert restored["import_warnings"] == []
    states = workspace_state(restore_db)["episodes"]
    assert [item["review_status"] for item in states] == ["completed", "completed"]
    assert [item["quality_decision"] for item in states] == ["pass", "pass"]
    assert [item["reviewer_name"] for item in states] == ["质检员1", "质检员2"]
    assert [item["annotation_count"] for item in states] == [0, 0]


def test_v1_import_isolates_duplicate_annotation_ids_between_tasks(tmp_path: Path):
    source_a = tmp_path / "task_a"
    _write_sample_episode(source_a / "episode_000001")
    schema_path = _write_label_schema(tmp_path / "labels.yaml")

    author_db = tmp_path / "author.db"
    import_label_schema(author_db, schema_path)
    authored = scan_data_source(author_db, source_a)
    annotation = save_annotation(
        author_db,
        {
            "episode_id": authored["episodes"][0]["id"],
            "label_code": "camera_blur",
            "scope": "time_range",
            "start_offset_ns": 500_000_000,
            "end_offset_ns": 1_500_000_000,
            "target_type": "camera",
            "target_key": "/camera/ego_head/image/jpeg",
            "comment": "跨任务 ID 隔离",
        },
    )
    exported = export_workspace(author_db, tmp_path / "exports", export_format="json")
    exported_file = Path(exported["output_file"])
    shutil.copy2(exported_file, source_a / exported_file.name)
    source_b = tmp_path / "task_b"
    shutil.copytree(source_a, source_b)

    restore_db = tmp_path / "restore.db"
    import_label_schema(restore_db, schema_path)
    first = scan_data_source(restore_db, source_a)
    second = scan_data_source(restore_db, source_b)

    first_episode_id = first["episodes"][0]["id"]
    second_episode_id = second["episodes"][0]["id"]
    first_detail = episode_detail(restore_db, first_episode_id)
    second_detail = episode_detail(restore_db, second_episode_id)
    assert first["import_warnings"] == []
    assert second["import_warnings"] == []
    assert first_detail["episode"]["annotation_count"] == 1
    assert len(first_detail["annotations"]) == 1
    assert second_detail["episode"]["annotation_count"] == 1
    assert len(second_detail["annotations"]) == 1
    assert first_detail["annotations"][0]["annotation_id"] == annotation["annotation_id"]
    assert first_detail["annotations"][0]["annotation_id"] != second_detail["annotations"][0]["annotation_id"]

    cleared = clear_local_task_history(restore_db, keep_task_id=second["task_id"])
    assert cleared["removed_count"] == 1
    rescanned = scan_data_source(restore_db, source_b)
    rescanned_detail = episode_detail(restore_db, second_episode_id)
    assert rescanned["restored_annotations"] == 1
    assert rescanned["import_warnings"] == []
    assert rescanned_detail["episode"]["annotation_count"] == 1
    assert len(rescanned_detail["annotations"]) == 1
    assert rescanned_detail["annotations"][0]["annotation_id"] == second_detail["annotations"][0]["annotation_id"]


def test_qc_tasks_isolate_episode_lists_and_reuse_same_source(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    first_root = tmp_path / "资产甲"
    second_root = tmp_path / "资产乙"
    _write_sample_episode(first_root / "episode_000001")
    _write_sample_episode(second_root / "episode_000001")

    first = scan_data_source(db_path, first_root)
    second = scan_data_source(db_path, second_root)

    tasks = list_qc_tasks(db_path)
    assert len(tasks) == 2
    assert {item["task_name"] for item in tasks} == {"资产甲", "资产乙"}
    assert all(item["episode_count"] == 1 for item in tasks)
    assert workspace_state(db_path, task_id=first["task_id"])["selected_task"]["task_name"] == "资产甲"
    assert [
        item["episode_name"] for item in workspace_state(db_path, task_id=first["task_id"])["episodes"]
    ] == ["episode_000001"]
    assert all(
        item["task_id"] == first["task_id"]
        for item in workspace_state(db_path, task_id=first["task_id"])["episodes"]
    )
    assert len(workspace_state(db_path)["episodes"]) == 2

    rescanned = scan_data_source(db_path, first_root)
    assert rescanned["existing_task"] is True
    assert rescanned["task_id"] == first["task_id"]
    assert len(list_qc_tasks(db_path)) == 2

    exported = export_workspace(
        db_path,
        tmp_path / "task-export",
        task_id=second["task_id"],
    )
    assert exported["task_id"] == second["task_id"]
    assert exported["task_name"] == "资产乙"
    assert exported["episode_count"] == 1


def test_schema_v1_data_source_is_migrated_to_qc_task(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    source_root = tmp_path / "旧资产"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE workspace (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, reviewer_name TEXT NOT NULL DEFAULT '',
                active_label_set_id TEXT, settings_json TEXT NOT NULL DEFAULT '{}',
                schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE data_source (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspace(id),
                root_path TEXT NOT NULL UNIQUE, profile_id TEXT, profile_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1, last_scanned_at TEXT, created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO workspace VALUES ('ws_legacy', '旧工作区', '', NULL, '{}', 1, '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO data_source VALUES ('src_legacy', 'ws_legacy', ?, NULL, '{}', 1, '2026-08-01T01:00:00+00:00', '2026-08-01T00:00:00+00:00')",
            (str(source_root),),
        )

    workspace = initialize_workspace(db_path)
    tasks = list_qc_tasks(db_path)

    assert workspace["schema_version"] == 5
    assert len(tasks) == 1
    assert tasks[0]["task_name"] == "旧资产"
    assert tasks[0]["local_source_path"] == str(source_root)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT task_id FROM data_source").fetchone()[0] == tasks[0]["id"]
    assert {"review_started_at", "review_completed_at"}.issubset(
        _table_columns(db_path, "qc_task")
    )
    assert "previous_review_json" in _table_columns(db_path, "episode")


def test_v1_bad_episode_does_not_block_valid_episode(tmp_path: Path):
    root = tmp_path / "dataset"
    _write_sample_episode(root / "episode_000001")
    broken = root / "episode_000002"
    broken.mkdir(parents=True)
    (broken / "episode.mcap").write_bytes(b"not an mcap")

    result = scan_data_source(tmp_path / "workspace.db", root)

    assert result["discovered"] == 2
    assert result["ready"] == 1
    assert result["failed"] == 1
    errors = [episode for episode in result["episodes"] if episode["import_status"] == "failed"]
    assert len(errors) == 1
    assert "EndOfFile" in errors[0]["import_error"] or "magic" in errors[0]["import_error"].lower()


def test_v1_rescan_skips_unchanged_ready_episode(tmp_path: Path):
    root = tmp_path / "dataset"
    _write_sample_episode(root / "episode_000001")
    db_path = tmp_path / "workspace.db"

    first = scan_data_source(db_path, root)
    second = scan_data_source(db_path, root)

    assert first["unchanged"] == 0
    assert second["unchanged"] == 1
    assert second["episodes"][0]["unchanged"] is True


def test_v1_priority_cache_is_usable_before_full_cache(tmp_path: Path):
    root = tmp_path / "dataset"
    _write_sample_episode(root / "episode_000001")
    db_path = tmp_path / "workspace.db"
    episode_id = scan_data_source(db_path, root)["episodes"][0]["id"]
    cache_root = tmp_path / "cache"

    priority = prepare_episode_cache(db_path, episode_id, cache_root, mode="priority")

    assert priority["cache_mode"] == "priority"
    assert priority["complete"] is False
    assert [item["topic"] for item in priority["cameras"]] == ["/camera/ego_head/image/jpeg"]
    assert priority["motion"]["available"] is False
    assert [item["key"] for item in priority["robot_actions"]["sources"]] == ["policy"]
    assert read_cached_camera_frame(
        priority["manifest_path"],
        priority["cameras"][0]["stream_id"],
        1_050_000_000,
    )["frame_index"] == 1
    assert read_cached_robot_action_frame(
        priority["manifest_path"],
        "policy",
        1_050_000_000,
    )["joint_positions"] == pytest.approx([1 + joint / 100 for joint in range(29)])
    priority_camera_file = Path(priority["manifest_path"]).parent / priority["cameras"][0]["frames_file"]

    full = prepare_episode_cache(db_path, episode_id, cache_root, mode="full")

    assert full["cache_mode"] == "full"
    assert full["complete"] is True
    assert full["motion"]["available"] is True
    assert {item["key"] for item in full["robot_actions"]["sources"]} == {
        "policy", "policy_target", "policy_command", "soma",
    }
    full_camera_file = Path(full["manifest_path"]).parent / full["cameras"][0]["frames_file"]
    assert priority_camera_file.stat().st_ino == full_camera_file.stat().st_ino
    reused = prepare_episode_cache(db_path, episode_id, cache_root, mode="priority")
    assert reused["cache_mode"] == "full"
    assert reused["reused"] is True


def test_v1_label_schema_reports_conflicts(tmp_path: Path):
    path = _write_label_schema(tmp_path / "labels.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["labels"][1]["code"] = payload["labels"][0]["code"]
    payload["labels"][1]["shortcut"] = payload["labels"][0]["shortcut"]
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

    preview = preview_label_schema(tmp_path / "workspace.db", path)

    assert preview["valid"] is False
    assert any("标签编码重复" in error for error in preview["errors"])
    assert any("标签快捷键重复" in error for error in preview["errors"])


def test_v1_label_schema_supports_json_and_csv(tmp_path: Path):
    yaml_path = _write_label_schema(tmp_path / "labels.yaml")
    json_path = tmp_path / "labels.json"
    json_path.write_text(json.dumps(yaml.safe_load(yaml_path.read_text(encoding="utf-8")), ensure_ascii=False), encoding="utf-8")
    csv_path = tmp_path / "custom_labels.csv"
    csv_path.write_text(
        "code,name,group,description,enabled,annotation_scopes,target_types,default_severity,default_action,shortcut,color,applicable_profiles\n"
        "camera_freeze,画面冻结,camera,画面停止更新,true,time_range|time_point,camera,normal,trim,C,#6366F1,g1_soma_inspire_v1\n",
        encoding="utf-8",
    )

    json_preview = preview_label_schema(tmp_path / "json-workspace.db", json_path)
    csv_preview = preview_label_schema(tmp_path / "csv-workspace.db", csv_path)

    assert json_preview["valid"] is True
    assert json_preview["source_format"] == "json"
    assert csv_preview["valid"] is True
    assert csv_preview["source_format"] == "csv"
    label = csv_preview["schema"]["labels"][0]
    assert label["annotation_scopes"] == ["time_range", "time_point"]
    assert label["target_types"] == ["camera"]


def test_simple_chinese_label_template_fills_internal_defaults_and_imports(tmp_path: Path):
    schema_path = tmp_path / "标注规范.yaml"
    schema_path.write_text(
        """标签库名称: 洗衣机任务标签
版本: "2.0"
标签:
  - 编码: unnatural_motion
    名称: 动作不自然
  - 编码: clothes_drop
    名称: 衣物掉落
    分组: 衣物处理
    说明: 衣物从手中或目标位置掉落
    范围: 时间点、区间
    对象: 画面、动捕
    严重程度: 严重
    处理建议: 待复核
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "workspace.db"

    preview = preview_label_schema(db_path, schema_path)

    assert preview["valid"] is True
    assert preview["template_mode"] == "simple"
    assert preview["schema"]["schema"]["label_set_name"] == "洗衣机任务标签"
    minimal, detailed = preview["schema"]["labels"]
    assert minimal["code"] == "unnatural_motion"
    assert minimal["annotation_scopes"] == ["time_range", "time_point", "episode"]
    assert minimal["target_types"] == ["global"]
    assert minimal["default_severity"] == "normal"
    assert minimal["default_action"] == "keep_with_label"
    assert detailed["annotation_scopes"] == ["time_point", "time_range"]
    assert detailed["target_types"] == ["camera", "mocap"]
    assert detailed["default_severity"] == "critical"
    assert detailed["default_action"] == "review"

    imported = import_label_schema(db_path, schema_path)
    active = workspace_state(db_path)["label_schema"]
    assert imported["active"] is True
    assert active["schema"]["label_set_name"] == "洗衣机任务标签"
    assert [item["name"] for item in active["labels"]] == ["动作不自然", "衣物掉落"]


def test_simple_chinese_label_template_reports_plain_language_errors(tmp_path: Path):
    schema_path = tmp_path / "错误模板.yaml"
    schema_path.write_text(
        """标签库名称: 测试标签
标签:
  - 编码: clothes_drop
    名称: 衣物掉落
    范围: 一小会儿
""",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="标签“衣物掉落”的范围“一小会儿”无法识别；可填写：区间、时间点、整条、全部",
    ):
        preview_label_schema(tmp_path / "workspace.db", schema_path)


@pytest.mark.parametrize(
    ("label", "message"),
    [
        ("名称: 衣物掉落", "标签“衣物掉落”没有填写“编码”"),
        (
            "编码: 衣物-掉落\n    名称: 衣物掉落",
            "标签“衣物掉落”的编码“衣物-掉落”格式不正确",
        ),
        (
            "编码: ClothesDrop\n    名称: 衣物掉落",
            "标签“衣物掉落”的编码“ClothesDrop”格式不正确",
        ),
    ],
)
def test_simple_chinese_label_template_requires_readable_code(
    tmp_path: Path, label: str, message: str
):
    schema_path = tmp_path / "编码错误.yaml"
    schema_path.write_text(
        f"标签库名称: 测试标签\n标签:\n  - {label}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=re.escape(message)):
        preview_label_schema(tmp_path / "workspace.db", schema_path)


def test_simple_chinese_csv_template_uses_readable_columns(tmp_path: Path):
    csv_path = tmp_path / "洗衣机标签.csv"
    csv_path.write_text(
        "编码,标签名称,分组,判断标准,范围,对象,严重程度,处理建议\n"
        "clothes_drop,衣物掉落,衣物处理,衣物从手中掉落,时间点或区间,画面,严重,待复核\n",
        encoding="utf-8",
    )

    preview = preview_label_schema(tmp_path / "workspace.db", csv_path)

    assert preview["valid"] is True
    assert preview["template_mode"] == "simple"
    label = preview["schema"]["labels"][0]
    assert label["code"] == "clothes_drop"
    assert label["name"] == "衣物掉落"
    assert label["annotation_scopes"] == ["time_point", "time_range"]
    assert label["target_types"] == ["camera"]


@pytest.mark.parametrize(
    ("relative_path", "expected_count"),
    [
        ("app/renderer/label-template-simple.yaml", 6),
        ("mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_simple.yaml", 9),
        ("mocap_qc_v1_design_bundle/label_templates/washing_machine_task_qc_simple.csv", 9),
    ],
)
def test_shipped_simple_label_templates_are_importable(
    tmp_path: Path, relative_path: str, expected_count: int
):
    project_root = Path(__file__).resolve().parents[1]

    preview = preview_label_schema(
        tmp_path / f"workspace-{expected_count}-{Path(relative_path).suffix}.db",
        project_root / relative_path,
    )

    assert preview["valid"] is True
    assert preview["template_mode"] == "simple"
    assert len(preview["schema"]["labels"]) == expected_count


def test_v1_annotation_rejects_out_of_bounds_time(tmp_path: Path):
    root = tmp_path / "dataset"
    _write_sample_episode(root / "episode_000001")
    db_path = tmp_path / "workspace.db"
    episode_id = scan_data_source(db_path, root)["episodes"][0]["id"]
    import_label_schema(db_path, _write_label_schema(tmp_path / "labels.yaml"))

    with pytest.raises(ValueError, match="时间越界"):
        save_annotation(
            db_path,
            {
                "episode_id": episode_id,
                "label_code": "camera_blur",
                "scope": "time_range",
                "start_offset_ns": 0,
                "end_offset_ns": 4_000_000_000,
                "target_type": "camera",
                "target_key": "/camera/ego_head/image/jpeg",
            },
        )


def _write_sample_episode(directory: Path) -> Path:
    directory.mkdir(parents=True)
    path = directory / "episode.mcap"
    image_schema = b"foxglove compressed image"
    start = 10_000_000_000
    with path.open("wb") as output:
        writer = Writer(output, compression=CompressionType.NONE)
        writer.start(profile="test", library="episode-qc-test")
        schema_id = writer.register_schema("foxglove.CompressedImage", "protobuf", image_schema)
        camera = writer.register_channel("/camera/ego_head/image/jpeg", "protobuf", schema_id)
        writer.register_channel("/camera/x5/panorama/image/jpeg", "protobuf", schema_id)
        mocap = writer.register_channel("/mocap/human_motion", "json", 0)
        policy = writer.register_channel("/g1/policy/controller_context", "msgpack", 0)
        policy_target = writer.register_channel("/g1/policy/input_ref_motion_cmd", "msgpack", 0)
        policy_command = writer.register_channel("/g1/policy/final_action", "msgpack", 0)
        soma = writer.register_channel("/soma/retarget/action", "msgpack", 0)
        for index in range(3):
            timestamp = start + index * 1_000_000_000
            writer.add_message(camera, timestamp, _compressed_image_payload(_jpeg(index)), timestamp, index)
            writer.add_message(mocap, timestamp + 10_000_000, _motion_payload(index), timestamp + 10_000_000, index)
            writer.add_message(policy, timestamp + 20_000_000, _policy_context_payload(index), timestamp + 20_000_000, index)
            writer.add_message(policy_target, timestamp + 25_000_000, _policy_reference_payload(index), timestamp + 25_000_000, index)
            writer.add_message(policy_command, timestamp + 27_000_000, _policy_payload(index), timestamp + 27_000_000, index)
            writer.add_message(soma, timestamp + 30_000_000, _soma_payload(index), timestamp + 30_000_000, index)
        writer.finish()
    (directory / "metadata.yaml").write_text("status: saved\n", encoding="utf-8")
    (directory / "config_snapshot.yaml").write_text("streams: []\n", encoding="utf-8")
    return path


def test_label_library_lists_activates_and_soft_deletes_versions(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    first_path = _write_label_schema(tmp_path / "labels-v1.yaml")
    first = import_label_schema(db_path, first_path)
    payload = yaml.safe_load(first_path.read_text(encoding="utf-8"))
    payload["schema"]["schema_version"] = "2.0.0"
    payload["schema"]["label_set_name"] = "测试标签新版"
    second_path = tmp_path / "labels-v2.yaml"
    second_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    second = import_label_schema(db_path, second_path)

    label_sets = list_label_sets(db_path)
    assert len(label_sets) == 2
    assert label_sets[0]["active"] is True
    assert label_sets[0]["label_count"] == 2
    first_id = next(item["id"] for item in label_sets if item["version"] == first["version"])
    second_id = next(item["id"] for item in label_sets if item["version"] == second["version"])

    activated = activate_label_set(db_path, first_id)
    assert activated["active"] is True
    deleted = delete_label_set(db_path, first_id)
    assert deleted["replacement_id"] == second_id
    assert [item["id"] for item in list_label_sets(db_path)] == [second_id]
    with pytest.raises(ValueError, match="至少保留一个"):
        delete_label_set(db_path, second_id)


def test_clear_local_task_history_keeps_current_and_flow_tasks_and_source_files(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    first_source = tmp_path / "local-current"
    second_source = tmp_path / "local-history"
    flow_source = tmp_path / "flow-cache"
    _write_sample_episode(first_source / "episode_000001")
    _write_sample_episode(second_source / "episode_000002")
    _write_sample_episode(flow_source / "episode_000003")
    current = scan_data_source(db_path, first_source)
    historical = scan_data_source(db_path, second_source)
    flow = scan_data_source(
        db_path,
        flow_source,
        task_code="QCJ-TEST-001",
        task_name="Flow 任务",
        origin="flow",
        flow_job_code="QCJ-TEST-001",
    )

    cleared = clear_local_task_history(db_path, keep_task_id=current["task_id"])
    assert cleared["removed_count"] == 1
    assert cleared["removed_tasks"][0]["id"] == historical["task_id"]
    assert cleared["source_files_deleted"] is False
    assert second_source.is_dir()
    remaining_ids = {item["id"] for item in list_qc_tasks(db_path)}
    assert remaining_ids == {current["task_id"], flow["task_id"]}


def _write_label_schema(path: Path) -> Path:
    payload = {
        "schema": {
            "schema_type": "annotation_label_schema",
            "schema_version": "1.0.0",
            "label_set_id": "test_labels",
            "label_set_name": "测试标签",
            "language": "zh-CN",
        },
        "severity_levels": [{"code": "normal", "name": "一般", "order": 1}],
        "actions": [{"code": "trim", "name": "裁剪"}],
        "groups": [{"code": "camera", "name": "相机", "order": 1}, {"code": "mocap", "name": "Mocap", "order": 2}],
        "labels": [
            {
                "code": "camera_blur", "name": "画面模糊", "group": "camera", "enabled": True,
                "annotation_scopes": ["episode", "time_range"], "target_types": ["camera"],
                "default_severity": "normal", "default_action": "trim", "shortcut": "B", "color": "#8844EE",
            },
            {
                "code": "mocap_joint_jitter", "name": "关节抖动", "group": "mocap", "enabled": True,
                "annotation_scopes": ["time_range", "time_point"], "target_types": ["mocap", "joint"],
                "default_severity": "normal", "default_action": "trim", "shortcut": "Q", "color": "#EE9900",
                "fields": [{"code": "affected_joint", "name": "异常关节", "type": "joint_selector", "required": True, "multiple": True}],
            },
        ],
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _label_set_storage_state(db_path: Path) -> dict[str, object]:
    with sqlite3.connect(db_path) as connection:
        workspace = connection.execute("SELECT * FROM workspace LIMIT 1").fetchone()
        label_set = connection.execute(
            "SELECT id, source_hash, enabled, raw_schema_json FROM label_set"
        ).fetchone()
        definitions = connection.execute(
            "SELECT * FROM label_definition ORDER BY id"
        ).fetchall()
        active_id = connection.execute(
            "SELECT active_label_set_id FROM workspace LIMIT 1"
        ).fetchone()[0]
    return {
        "workspace": workspace,
        "id": label_set[0],
        "source_hash": label_set[1],
        "enabled": label_set[2],
        "raw_schema_json": label_set[3],
        "definitions": definitions,
        "active_id": active_id,
    }


def _table_columns(db_path: Path, table: str) -> tuple[str, ...]:
    with sqlite3.connect(db_path) as connection:
        return tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))


def _jpeg(index: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 12), (index * 50, 80, 120)).save(output, format="JPEG")
    return output.getvalue()


def _compressed_image_payload(jpeg: bytes) -> bytes:
    return _field(2, jpeg) + _field(3, b"jpeg") + _field(4, b"ego_head")


def _field(number: int, value: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _varint(value: int) -> bytes:
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def _motion_payload(index: int) -> bytes:
    return json.dumps(
        {
            "schema": "mocap_human_motion.raw_v1",
            "sequence": index,
            "source_timestamp_ns": 10_000_000_000 + index * 1_000_000_000,
            "motion": {
                "format": "link_pose_float32",
                "link_order": ["Hips", "Head"],
                "links": {
                    "Hips": {"position": [index * 0.1, 0, 0], "quat_wxyz": [1, 0, 0, 0]},
                    "Head": {"position": [index * 0.1, 0, 1], "quat_wxyz": [1, 0, 0, 0]},
                },
            },
        }
    ).encode("utf-8")


def _policy_payload(index: int) -> bytes:
    final = [50 + index + joint / 100 for joint in range(29)]
    return _messagepack(
        {
            "schema": "g1_policy_final_action.v1",
            "sequence": index,
            "source_timestamp_ns": 10_000_000_000 + index * 1_000_000_000,
            "action": {
                "raw_policy_action": [-999.0] * 29,
                "pre_clip_q_target": [-888.0] * 29,
                "final_q_target": final,
            },
        }
    )


def _policy_reference_payload(index: int) -> bytes:
    isaaclab_joints = [150 + index + joint / 100 for joint in range(29)]
    return _messagepack(
        {
            "schema": "g1_policy_input_ref_motion_cmd.v1",
            "sequence": index,
            "source_timestamp_ns": 10_000_000_000 + index * 1_000_000_000,
            "cmd": {
                "reference_source": "pmg",
                "reference_sequence": 100 + index,
                "qpos": isaaclab_joints,
                "body_pos": [[1 + index / 10, 1.2, 1.3]],
                "body_quat": [[0.5, 0.5, -0.5, -0.5]],
            },
        }
    )


def _policy_context_payload(index: int) -> bytes:
    body_q = [index + joint / 100 for joint in range(29)]
    return _messagepack(
        {
            "schema": "g1_policy_controller_context.v1",
            "sequence": index,
            "source_timestamp_ns": 10_000_000_000 + index * 1_000_000_000,
            "context": {"body_q": body_q, "base_quat": [1.0, 0.0, 0.0, 0.0]},
        }
    )


def _soma_payload(index: int) -> bytes:
    joints = [100 + index + joint / 100 for joint in range(29)]
    return _messagepack(
        {
            "schema": "soma_retarget_action.v1",
            "sequence": index,
            "source_timestamp_ns": 10_000_000_000 + index * 1_000_000_000,
            "action": {
                "qpos": [0.1, 0.2, 0.3, 1.0, 0.0, 0.0, 0.0, *joints],
                "qpos_shape": [36],
                "root_quat_order": "wxyz",
            },
        }
    )


def _messagepack(value: object) -> bytes:
    if value is None:
        return b"\xc0"
    if value is False:
        return b"\xc2"
    if value is True:
        return b"\xc3"
    if isinstance(value, int):
        if 0 <= value <= 0x7F:
            return bytes([value])
        if 0 <= value <= 0xFFFFFFFFFFFFFFFF:
            return b"\xcf" + struct.pack(">Q", value)
        return b"\xd3" + struct.pack(">q", value)
    if isinstance(value, float):
        return b"\xcb" + struct.pack(">d", value)
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) <= 31:
            return bytes([0xA0 | len(encoded)]) + encoded
        return b"\xd9" + struct.pack(">B", len(encoded)) + encoded
    if isinstance(value, (list, tuple)):
        prefix = bytes([0x90 | len(value)]) if len(value) <= 15 else b"\xdc" + struct.pack(">H", len(value))
        return prefix + b"".join(_messagepack(item) for item in value)
    if isinstance(value, dict):
        prefix = bytes([0x80 | len(value)]) if len(value) <= 15 else b"\xde" + struct.pack(">H", len(value))
        return prefix + b"".join(_messagepack(key) + _messagepack(item) for key, item in value.items())
    raise TypeError(f"测试 MessagePack 编码器不支持: {type(value).__name__}")
