from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from mcap.writer import CompressionType, Writer

import episode_qc.playback as playback
from episode_qc.playback import prepare_episode_cache, read_cached_motion_frame
from episode_qc.workspace import (
    episode_detail,
    export_workspace,
    preview_label_schema,
    save_annotation,
    scan_data_source,
    workspace_state,
)


CAMERA_TOPICS = [
    "/dohc/cam0/image/compressed",
    "/dohc/cam1/image/compressed",
    "/dohc/cam2/image/compressed",
    "/dohc/t265_left/image/compressed",
    "/dohc/t265_right/image/compressed",
]


def test_omniego_files_are_indexed_as_episodes_with_five_cameras_and_pose(tmp_path: Path):
    root = tmp_path / "OmniEgo"
    _write_omniego_mcap(root / "picking" / "picking_001.mcap")
    _write_omniego_mcap(root / "table" / "table_001.mcap")
    db_path = tmp_path / "workspace.db"

    indexed = scan_data_source(db_path, root, task_kind="ego_omniego")

    assert indexed["discovered"] == 2
    assert indexed["ready"] == 2
    assert indexed["task"]["task_kind"] == "ego_omniego"
    assert [item["relative_path"] for item in indexed["episodes"]] == [
        "picking/picking_001.mcap",
        "table/table_001.mcap",
    ]
    assert [item["episode_name"] for item in indexed["episodes"]] == [
        "picking_001",
        "table_001",
    ]
    assert all(item["camera_count"] == 5 for item in indexed["episodes"])
    assert all(item["mocap_available"] for item in indexed["episodes"])

    episode = indexed["episodes"][0]
    detail = episode_detail(db_path, episode["id"])
    skeleton = next(item for item in detail["streams"] if item["topic"] == "/dohc/skeleton")
    assert skeleton["adapter_id"] == "dohc_smpl_24_v1"

    manifest = prepare_episode_cache(
        db_path,
        episode["id"],
        tmp_path / "playback-cache",
        mode="full",
    )
    assert len(manifest["cameras"]) == 5
    assert all(item["message_count"] == 1 for item in manifest["cameras"])
    assert manifest["motion"]["available"] is True
    assert manifest["motion"]["adapter_id"] == "dohc_smpl_24_v1"
    assert manifest["motion"]["joint_names"][0:4] == [
        "pelvis",
        "left_hip",
        "right_hip",
        "spine1",
    ]
    assert manifest["motion"]["parent_indices"][0:4] == [-1, 0, 0, 0]
    frame = read_cached_motion_frame(manifest["manifest_path"], 0)
    assert frame["positions"][4] == pytest.approx([0.1, 0.2, -0.4])
    assert frame["sequence"] == 7


def test_actual_omniego_video_and_body_schemas_prepare_camera_and_pose_cache(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "OmniEgo"
    _write_actual_omniego_mcap(root / "coffee" / "coffee_001.mcap")
    db_path = tmp_path / "workspace.db"

    indexed = scan_data_source(db_path, root, task_kind="ego_omniego")

    episode = indexed["episodes"][0]
    assert episode["camera_count"] == 2
    assert episode["mocap_available"] is True
    detail = episode_detail(db_path, episode["id"])
    streams = {item["topic"]: item for item in detail["streams"]}
    assert streams["/sensor/camera/head_left/video"]["adapter_id"] == (
        "foxglove_compressed_video_h264_v1"
    )
    assert streams["/pose/body"]["adapter_id"] == "omniego_body_frame_v1"

    def fake_transcode(_source: Path, destination: Path) -> None:
        destination.write_bytes(
            b"\xff\xd8first-frame\xff\xd9\xff\xd8second-frame\xff\xd9"
        )

    monkeypatch.setattr(playback, "_transcode_h264_to_mjpeg", fake_transcode)
    manifest = prepare_episode_cache(
        db_path,
        episode["id"],
        tmp_path / "playback-cache",
        mode="full",
    )

    assert [item["message_count"] for item in manifest["cameras"]] == [2, 2]
    assert manifest["motion"]["available"] is True
    assert manifest["motion"]["message_count"] == 2
    assert manifest["motion"]["joint_names"] == playback.SMPL_24_JOINT_NAMES[:22]
    assert manifest["motion"]["parent_indices"] == playback.SMPL_24_PARENT_INDICES[:22]
    frame = read_cached_motion_frame(manifest["manifest_path"], 0)
    assert frame["positions"][0] == pytest.approx([0.1, -0.2, -0.7])
    assert frame["rotations_wxyz"][0] == pytest.approx([1.0, 0.0, 0.0, 0.0])


def test_packaged_ego_manual_label_schema_covers_actions_exceptions_and_pose(tmp_path: Path):
    schema_path = Path(__file__).resolve().parents[1] / "app" / "renderer" / "label-schema-ego-manual.yaml"

    preview = preview_label_schema(tmp_path / "workspace.db", schema_path)

    assert preview["valid"] is True
    labels = {item["code"]: item for item in preview["schema"]["labels"]}
    assert {"transport_object", "pick_object", "place_object", "arrange_object"} <= labels.keys()
    assert {field["code"] for field in labels["pick_object"]["fields"]} >= {
        "body_part",
        "object_name",
        "object_color",
        "target_name",
    }
    assert labels["unexpected_event"]["default_action"] == "keep_with_label"
    assert labels["pose_misaligned"]["target_types"] == ["mocap", "joint"]


def test_ego_uses_open_labels_without_a_label_library_and_exports_a_snapshot(tmp_path: Path):
    root = tmp_path / "OmniEgo"
    _write_omniego_mcap(root / "coffee" / "coffee_001.mcap")
    db_path = tmp_path / "workspace.db"

    indexed = scan_data_source(db_path, root, task_kind="ego_omniego")
    task = indexed["task"]
    assert task["annotation_mode"] == "open"
    assert task["annotation_schema_version"] == "ego_open_v1"
    assert task["label_set_id"] is None
    schema = workspace_state(db_path, task_id=task["id"])["label_schema"]
    assert schema["schema"]["annotation_mode"] == "open"
    assert any(item["code"] == "joint_misaligned_2d" for item in schema["labels"])

    episode = indexed["episodes"][0]
    saved = save_annotation(
        db_path,
        {
            "episode_id": episode["id"],
            "annotation_type": "action",
            "label_name": "双手拿起红色咖啡杯",
            "scope": "episode",
            "start_offset_ns": 0,
            "end_offset_ns": 0,
            "target_type": "mocap",
            "severity": "normal",
            "action": "keep",
            "attributes": {
                "body_part": "both_hands",
                "object_name": "咖啡杯",
                "object_color": "红色",
            },
        },
    )
    assert saved["annotation_mode"] == "open"
    assert saved["annotation_schema_version"] == "ego_open_v1"
    assert saved["label_name"] == "双手拿起红色咖啡杯"
    assert saved["label_slug"].startswith("custom_")
    assert saved["label_snapshot"]["label_name"] == "双手拿起红色咖啡杯"

    exported = export_workspace(db_path, tmp_path / "exports", task_id=task["id"])
    document = json.loads(Path(exported["output_file"]).read_text(encoding="utf-8"))
    assert document["annotations"][0]["annotation_mode"] == "open"
    assert document["annotations"][0]["label_name"] == "双手拿起红色咖啡杯"


def _write_omniego_mcap(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = 10_000_000_000
    with path.open("wb") as output:
        writer = Writer(output, compression=CompressionType.NONE)
        writer.start(profile="omniego-test", library="episode-qc-test")
        camera_schema = writer.register_schema(
            "sensor_msgs/CompressedImage",
            "ros1msg",
            b"std_msgs/Header header\nstring format\nuint8[] data\n",
        )
        camera_channels = [
            writer.register_channel(topic, "ros1", camera_schema)
            for topic in CAMERA_TOPICS
        ]
        json_schema = writer.register_schema("dohc.JsonMessage", "jsonschema", b"{}")
        skeleton = writer.register_channel("/dohc/skeleton", "json", json_schema)
        for channel in camera_channels:
            writer.add_message(
                channel,
                start,
                _ros1_compressed_image(b"\xff\xd8omniego\xff\xd9"),
                start,
                0,
            )
        writer.add_message(skeleton, start, _skeleton_payload(), start, 0)
        writer.finish()


def _write_actual_omniego_mcap(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = 10_000_000_000
    with path.open("wb") as output:
        writer = Writer(output, compression=CompressionType.NONE)
        writer.start(profile="omniego-actual-test", library="episode-qc-test")
        video_schema = writer.register_schema(
            "foxglove.CompressedVideo",
            "protobuf",
            b"descriptor-set-placeholder",
        )
        body_schema = writer.register_schema(
            "pose.BodyFrame",
            "protobuf",
            b"descriptor-set-placeholder",
        )
        cameras = [
            writer.register_channel(topic, "protobuf", video_schema)
            for topic in (
                "/sensor/camera/head_left/video",
                "/sensor/camera/head_right/video",
            )
        ]
        body = writer.register_channel("/pose/body", "protobuf", body_schema)
        for index in range(2):
            timestamp = start + index * 40_000_000
            for camera in cameras:
                writer.add_message(
                    camera,
                    timestamp,
                    _compressed_video_payload(index),
                    timestamp,
                    index,
                )
            writer.add_message(
                body,
                timestamp,
                _body_frame_payload(index, timestamp),
                timestamp,
                index,
            )
        writer.finish()


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _wire_bytes(field_number: int, value: bytes) -> bytes:
    return _varint((field_number << 3) | 2) + _varint(len(value)) + value


def _wire_uint(field_number: int, value: int) -> bytes:
    return _varint(field_number << 3) + _varint(value)


def _wire_double(field_number: int, value: float) -> bytes:
    return _varint((field_number << 3) | 1) + struct.pack("<d", value)


def _compressed_video_payload(index: int) -> bytes:
    return b"".join(
        [
            _wire_bytes(2, b"head_camera"),
            _wire_bytes(3, b"\x00\x00\x00\x01" + bytes([index + 1])),
            _wire_bytes(4, b"h264"),
        ]
    )


def _body_frame_payload(index: int, timestamp: int) -> bytes:
    header = _wire_uint(1, index) + _wire_uint(2, timestamp)
    transforms = []
    for joint_index in range(22):
        position = b"".join(
            [
                _wire_double(1, 0.1 + joint_index * 0.01),
                _wire_double(2, -0.2),
                _wire_double(3, -0.7),
            ]
        )
        quaternion = b"".join(
            [
                _wire_double(1, 1.0),
                _wire_double(2, 0.0),
                _wire_double(3, 0.0),
                _wire_double(4, 0.0),
            ]
        )
        transforms.append(_wire_bytes(2, _wire_bytes(1, position) + _wire_bytes(2, quaternion)))
    return _wire_bytes(1, header) + b"".join(transforms)


def _ros1_compressed_image(image: bytes) -> bytes:
    frame_id = b"ego"
    image_format = b"jpeg"
    return b"".join(
        [
            struct.pack("<III", 0, 10, 0),
            struct.pack("<I", len(frame_id)),
            frame_id,
            struct.pack("<I", len(image_format)),
            image_format,
            struct.pack("<I", len(image)),
            image,
        ]
    )


def _skeleton_payload() -> bytes:
    positions = [[0.0, 0.0, 0.0] for _ in range(24)]
    positions[4] = [0.1, 0.2, -0.4]
    return json.dumps(
        {
            "frame_id": 7,
            "frame_index": 7,
            "timestamp_ns": 10_000_000_000,
            "datasets": {
                "smpl_joints": {
                    "source_index": 7,
                    "shape": [24, 3],
                    "dtype": "float32",
                    "data": positions,
                }
            },
        }
    ).encode("utf-8")
