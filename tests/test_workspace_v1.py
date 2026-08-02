from __future__ import annotations

import csv
import io
import json
import struct
from pathlib import Path

import pytest
import yaml
from mcap.writer import CompressionType, Writer
from PIL import Image

from episode_qc.playback import (
    G1_29_JOINT_NAMES,
    prepare_episode_cache,
    read_cached_camera_frame,
    read_cached_motion_frame,
    read_cached_robot_action_frame,
)
from episode_qc.workspace import (
    delete_annotation,
    episode_detail,
    export_workspace,
    import_label_schema,
    initialize_workspace,
    preview_label_schema,
    redo_annotation_change,
    save_annotation,
    scan_data_source,
    undo_annotation_change,
    update_episode_review,
    update_workspace_settings,
    WorkspaceConflictError,
    workspace_state,
)


def test_v1_import_playback_annotation_and_export_round_trip(tmp_path: Path):
    source_root = tmp_path / "含 空格的数据"
    mcap_path = _write_sample_episode(source_root / "episode_000001")
    source_before = (mcap_path.stat().st_size, mcap_path.stat().st_mtime_ns)
    db_path = tmp_path / "workspace" / "workspace.db"
    schema_path = _write_label_schema(tmp_path / "labels.yaml")

    workspace = initialize_workspace(db_path, reviewer_name="测试员")
    result = scan_data_source(db_path, source_root)

    assert workspace["schema_version"] == 1
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
    assert manifest["cache_version"] == 4
    assert manifest["motion"]["frame_encoding"] == "episode-qc-motion-f32-le-v1"
    assert manifest["robot_actions"]["default_source"] == "policy"
    assert {item["key"] for item in manifest["robot_actions"]["sources"] if item["available"]} == {
        "policy", "policy_target", "soma",
    }
    frame = read_cached_camera_frame(manifest["manifest_path"], manifest["cameras"][0]["stream_id"], 1_050_000_000)
    assert frame["frame_index"] == 1
    assert frame["jpeg"].startswith(b"\xff\xd8")
    motion = read_cached_motion_frame(manifest["manifest_path"], 1_050_000_000)
    assert motion["positions"][1] == pytest.approx([0.1, 0.0, 1.0])
    assert motion["parent_indices"] == [-1, -1]
    policy = read_cached_robot_action_frame(manifest["manifest_path"], "policy", 1_050_000_000)
    policy_target = read_cached_robot_action_frame(manifest["manifest_path"], "policy_target", 1_050_000_000)
    soma = read_cached_robot_action_frame(manifest["manifest_path"], "soma", 1_050_000_000)
    assert policy["joint_names"] == G1_29_JOINT_NAMES
    assert policy["joint_positions"] == pytest.approx([1 + joint / 100 for joint in range(29)])
    assert policy_target["joint_positions"] == pytest.approx([51 + joint / 100 for joint in range(29)])
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
    exported = export_workspace(db_path, tmp_path / "exports", completed_only=True)
    output = Path(exported["output_dir"])
    assert exported["episode_count"] == 1
    assert exported["annotation_count"] == 1
    assert output.name.startswith("含_空格的数据_qc_annotations_")
    assert exported["source_directories"] == [str(source_root.resolve())]
    assert {path.name for path in output.iterdir()} == {
        "annotations.jsonl", "annotations.csv", "episodes.csv", "label_schema.json", "export_manifest.json"
    }
    jsonl_rows = [json.loads(line) for line in (output / "annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    with (output / "annotations.csv").open(encoding="utf-8-sig", newline="") as source:
        csv_rows = list(csv.DictReader(source))
    assert len(jsonl_rows) == len(csv_rows) == 1
    assert jsonl_rows[0]["absolute_start_time_ns"] == 10_500_000_000
    assert jsonl_rows[0]["label_schema_version"] == "1.0.0"
    assert (mcap_path.stat().st_size, mcap_path.stat().st_mtime_ns) == source_before

    delete_annotation(db_path, annotation["annotation_id"])
    assert workspace_state(db_path)["episodes"][0]["annotation_count"] == 0


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
    assert {item["key"] for item in full["robot_actions"]["sources"]} == {"policy", "policy_target", "soma"}
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
        policy_target = writer.register_channel("/g1/policy/final_action", "msgpack", 0)
        soma = writer.register_channel("/soma/retarget/action", "msgpack", 0)
        for index in range(3):
            timestamp = start + index * 1_000_000_000
            writer.add_message(camera, timestamp, _compressed_image_payload(_jpeg(index)), timestamp, index)
            writer.add_message(mocap, timestamp + 10_000_000, _motion_payload(index), timestamp + 10_000_000, index)
            writer.add_message(policy, timestamp + 20_000_000, _policy_context_payload(index), timestamp + 20_000_000, index)
            writer.add_message(policy_target, timestamp + 25_000_000, _policy_payload(index), timestamp + 25_000_000, index)
            writer.add_message(soma, timestamp + 30_000_000, _soma_payload(index), timestamp + 30_000_000, index)
        writer.finish()
    (directory / "metadata.yaml").write_text("status: saved\n", encoding="utf-8")
    (directory / "config_snapshot.yaml").write_text("streams: []\n", encoding="utf-8")
    return path


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


def _policy_context_payload(index: int) -> bytes:
    body_q = [index + joint / 100 for joint in range(29)]
    return _messagepack(
        {
            "schema": "g1_policy_controller_context.v1",
            "sequence": index,
            "source_timestamp_ns": 10_000_000_000 + index * 1_000_000_000,
            "context": {"body_q": body_q},
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
