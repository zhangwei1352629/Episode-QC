from __future__ import annotations

import csv
import io
import json
import re
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
from episode_qc.platform_workflow import canonical_json_sha256
from episode_qc.workspace import (
    _activate_label_schema,
    activate_label_set,
    clear_local_task_history,
    delete_label_set,
    delete_annotation,
    episode_detail,
    export_workspace,
    import_flow_label_schema,
    import_label_schema,
    initialize_workspace,
    list_label_sets,
    list_qc_tasks,
    preview_label_schema,
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


def test_flow_label_schema_rejects_snapshot_hash_mismatch(tmp_path: Path):
    schema = {
        "schema": {
            "schema_type": "annotation_label_schema",
            "schema_version": "1.0.0",
            "label_set_id": "flow_task_quality",
            "label_set_name": "Flow 任务标签",
            "language": "zh-CN",
        },
        "severity_levels": [],
        "actions": [],
        "groups": [{"code": "quality", "name": "质量", "order": 1}],
        "labels": [
            {
                "code": "camera_occlusion",
                "name": "相机遮挡",
                "group": "quality",
                "enabled": True,
                "annotation_scopes": ["episode"],
                "target_types": ["global"],
                "fields": [],
            }
        ],
    }
    job = {
        "label_set_id": "flow_task_quality",
        "label_schema_version": "1.0.0",
        "label_schema_hash": canonical_json_sha256(schema),
        "label_schema": schema,
    }
    db_path = tmp_path / "workspace.db"

    tampered = json.loads(json.dumps(job, ensure_ascii=False))
    tampered["label_schema"]["labels"][0]["name"] = "被篡改的标签"
    with pytest.raises(ValueError, match="摘要"):
        import_flow_label_schema(db_path, tampered)

    imported = import_flow_label_schema(db_path, job)
    assert imported is not None
    assert imported["source_hash"] == job["label_schema_hash"]
    assert workspace_state(db_path)["label_schema"]["labels"][0]["name"] == "相机遮挡"


def test_flow_label_schema_upgrades_matching_legacy_local_digest(tmp_path: Path):
    schema = {
        "schema": {
            "schema_type": "annotation_label_schema",
            "schema_version": "1.0.0",
            "label_set_id": "flow_task_quality",
            "label_set_name": "Flow 任务标签",
            "language": "zh-CN",
        },
        "severity_levels": [],
        "actions": [],
        "groups": [{"code": "quality", "name": "质量", "order": 1}],
        "labels": [
            {
                "code": "camera_occlusion",
                "name": "相机遮挡",
                "group": "quality",
                "enabled": True,
                "annotation_scopes": ["episode"],
                "target_types": ["global"],
                "fields": [],
            }
        ],
    }
    job = {
        "label_set_id": "flow_task_quality",
        "label_schema_version": "1.0.0",
        "label_schema_hash": canonical_json_sha256(schema),
        "label_schema": schema,
    }
    db_path = tmp_path / "workspace.db"
    initialize_workspace(db_path)

    # A workstation upgraded from the former Flow contract may already have
    # this exact immutable schema recorded under the old declared digest.
    _activate_label_schema(
        db_path,
        schema,
        source_format="flow",
        source_hash="a" * 64,
    )

    imported = import_flow_label_schema(db_path, job)

    assert imported is not None
    assert imported["source_hash"] == job["label_schema_hash"]


def test_v1_import_playback_annotation_and_export_round_trip(tmp_path: Path):
    source_root = tmp_path / "含 空格的数据"
    mcap_path = _write_sample_episode(source_root / "episode_000001")
    source_before = (mcap_path.stat().st_size, mcap_path.stat().st_mtime_ns)
    db_path = tmp_path / "workspace" / "workspace.db"
    schema_path = _write_label_schema(tmp_path / "labels.yaml")

    workspace = initialize_workspace(db_path, reviewer_name="测试员")
    result = scan_data_source(db_path, source_root)

    assert workspace["schema_version"] == 2
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

    assert workspace["schema_version"] == 2
    assert len(tasks) == 1
    assert tasks[0]["task_name"] == "旧资产"
    assert tasks[0]["local_source_path"] == str(source_root)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT task_id FROM data_source").fetchone()[0] == tasks[0]["id"]


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
