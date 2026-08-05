from __future__ import annotations

import bisect
from functools import lru_cache
import json
import math
import os
import shutil
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO

from mcap.reader import make_reader

from episode_qc.bvh import iter_bvh_motion, read_bvh_header
from episode_qc.compressed_image import decode_compressed_image
from episode_qc.messagepack import decode_messagepack
from episode_qc.workspace import _json, _now, connect_workspace, episode_detail


PLAYBACK_CACHE_VERSION = 6
MOTION_FRAME_ENCODING = "episode-qc-motion-f32-le-v1"
ACTION_FRAME_ENCODING = "episode-qc-action-f32-le-v2"
ACTION_ROOT_POSITION = 1
ACTION_ROOT_QUATERNION = 2
DEFAULT_CAMERA_TOPIC = "/camera/ego_head/image/jpeg"
CACHE_MODES = {"priority", "full"}
ROBOT_ACTION_SPECS = {
    "/g1/policy/controller_context": {
        "key": "policy",
        "display_name": "Policy 实际执行姿态",
        "adapter_id": "g1_policy_controller_context_body_q_v1",
    },
    "/g1/policy/input_ref_motion_cmd": {
        "key": "policy_target",
        "display_name": "PMG 目标姿态",
        "adapter_id": "g1_policy_input_ref_motion_cmd_v1",
    },
    "/g1/policy/final_action": {
        "key": "policy_command",
        "display_name": "Policy 最终控制目标",
        "adapter_id": "g1_policy_final_action_v1",
    },
    "/soma/retarget/action": {
        "key": "soma",
        "display_name": "SOMA 重定向动作",
        "adapter_id": "soma_retarget_action_v1",
    },
}
G1_29_JOINT_NAMES = [
    "left_hip_pitch_joint", "left_hip_roll_joint", "left_hip_yaw_joint",
    "left_knee_joint", "left_ankle_pitch_joint", "left_ankle_roll_joint",
    "right_hip_pitch_joint", "right_hip_roll_joint", "right_hip_yaw_joint",
    "right_knee_joint", "right_ankle_pitch_joint", "right_ankle_roll_joint",
    "waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint",
    "left_shoulder_pitch_joint", "left_shoulder_roll_joint", "left_shoulder_yaw_joint",
    "left_elbow_joint", "left_wrist_roll_joint", "left_wrist_pitch_joint", "left_wrist_yaw_joint",
    "right_shoulder_pitch_joint", "right_shoulder_roll_joint", "right_shoulder_yaw_joint",
    "right_elbow_joint", "right_wrist_roll_joint", "right_wrist_pitch_joint", "right_wrist_yaw_joint",
]
# PMG reference joints are stored in the interleaved IsaacLab order used by the
# policy observation. Each output (MuJoCo/URDF) joint selects this IsaacLab index.
G1_MUJOCO_TO_ISAACLAB_INDICES = [
    0, 3, 6, 9, 13, 17,
    1, 4, 7, 10, 14, 18,
    2, 5, 8,
    11, 15, 19, 21, 23, 25, 27,
    12, 16, 20, 22, 24, 26, 28,
]
ROBOT_ACTION_KEYS = frozenset(str(spec["key"]) for spec in ROBOT_ACTION_SPECS.values())


HUMAN_PARENT_NAMES = {
    "LeftUpLeg": "Hips",
    "LeftLeg": "LeftUpLeg",
    "LeftFoot": "LeftLeg",
    "LeftToe": "LeftFoot",
    "RightUpLeg": "Hips",
    "RightLeg": "RightUpLeg",
    "RightFoot": "RightLeg",
    "RightToe": "RightFoot",
    "Spine1": "Hips",
    "Spine2": "Spine1",
    "Chest": "Spine2",
    "Neck": "Chest",
    "Head": "Neck",
    "LeftShoulder": "Chest",
    "LeftArm": "LeftShoulder",
    "LeftForeArm": "LeftArm",
    "LeftHand": "LeftForeArm",
    "RightShoulder": "Chest",
    "RightArm": "RightShoulder",
    "RightForeArm": "RightArm",
    "RightHand": "RightForeArm",
}


def prepare_episode_cache(
    db_path: str | Path,
    episode_id: str,
    cache_root: str | Path,
    *,
    force: bool = False,
    mode: str = "full",
) -> dict[str, object]:
    if mode not in CACHE_MODES:
        raise ValueError(f"不支持的缓存模式: {mode}")
    detail = episode_detail(db_path, episode_id)
    episode = detail["episode"]
    if episode["import_status"] != "ready":
        raise ValueError(f"Episode 尚未就绪: {episode['import_status']}")
    root = Path(cache_root).expanduser().resolve()
    episode_root = root / "episodes" / episode_id / str(episode["fingerprint"])
    full_manifest_path = episode_root / "full" / "stream_index.json"
    if mode == "priority" and not force:
        full_manifest = _load_valid_cache_manifest(full_manifest_path, str(episode["fingerprint"]))
        if full_manifest is not None:
            _set_cache_status(db_path, episode_id, "ready")
            return full_manifest | {"manifest_path": str(full_manifest_path), "reused": True}

    final_dir = episode_root / mode
    manifest_path = final_dir / "stream_index.json"
    if manifest_path.is_file() and not force:
        manifest = _load_valid_cache_manifest(manifest_path, str(episode["fingerprint"]))
        if manifest is not None:
            _set_cache_status(db_path, episode_id, "ready" if manifest.get("complete") else "partial")
            return manifest | {"manifest_path": str(manifest_path), "reused": True}

    episode_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".prepare-", dir=episode_root))
    _set_cache_status(db_path, episode_id, "preparing")
    all_camera_streams = [item for item in detail["streams"] if item["stream_type"] == "camera" and item["available"]]
    all_motion_streams = [item for item in detail["streams"] if item["stream_type"] == "mocap" and item["available"]]
    all_action_streams = [
        item for item in detail["streams"]
        if item["available"] and item["topic"] in ROBOT_ACTION_SPECS
    ]
    priority_manifest_path = episode_root / "priority" / "stream_index.json"
    priority_manifest = (
        _load_valid_cache_manifest(priority_manifest_path, str(episode["fingerprint"]))
        if mode == "full"
        else None
    )
    if mode == "priority":
        preferred_camera = next(
            (item for item in all_camera_streams if item["topic"] == DEFAULT_CAMERA_TOPIC),
            all_camera_streams[0] if all_camera_streams else None,
        )
        camera_streams = [preferred_camera] if preferred_camera else []
        motion_streams = []
        policy_stream = next(
            (item for item in all_action_streams if ROBOT_ACTION_SPECS[item["topic"]]["key"] == "policy"),
            all_action_streams[0] if all_action_streams else None,
        )
        action_streams = [policy_stream] if policy_stream else []
    else:
        reused_camera_topics = {
            item["topic"] for item in (priority_manifest or {}).get("cameras", [])
        }
        reused_action_topics = {
            item["topic"]
            for item in ((priority_manifest or {}).get("robot_actions") or {}).get("sources", [])
        }
        camera_streams = [item for item in all_camera_streams if item["topic"] not in reused_camera_topics]
        motion_streams = all_motion_streams
        action_streams = [item for item in all_action_streams if item["topic"] not in reused_action_topics]
    selected_topics = [item["topic"] for item in camera_streams + motion_streams + action_streams]
    camera_by_topic = {item["topic"]: item for item in camera_streams}
    motion_topics = {item["topic"] for item in motion_streams}
    camera_files: dict[str, BinaryIO] = {}
    camera_indices: dict[str, list[list[int]]] = {str(item["id"]): [] for item in camera_streams}
    motion_file: BinaryIO | None = None
    motion_index: list[list[int]] = []
    action_by_topic = {
        item["topic"]: ROBOT_ACTION_SPECS[item["topic"]] | {"stream": item}
        for item in action_streams
    }
    action_files: dict[str, BinaryIO] = {}
    action_indices: dict[str, list[list[int]]] = {
        str(ROBOT_ACTION_SPECS[item["topic"]]["key"]): [] for item in action_streams
    }
    joint_names: list[str] = []
    parent_indices: list[int] = []
    decode_errors: list[str] = []

    try:
        cameras_dir = temp_dir / "cameras"
        cameras_dir.mkdir()
        for item in camera_streams:
            camera_files[str(item["id"])] = (cameras_dir / f"{item['id']}.frames").open("wb")
        if motion_streams:
            motion_dir = temp_dir / "mocap"
            motion_dir.mkdir()
            motion_file = (motion_dir / "motion.frames").open("wb")
        if action_streams:
            actions_dir = temp_dir / "robot_actions"
            actions_dir.mkdir()
            for item in action_streams:
                key = str(ROBOT_ACTION_SPECS[item["topic"]]["key"])
                action_files[key] = (actions_dir / f"{key}.frames").open("wb")

        start_ns = int(episode["start_time_ns"] or 0)
        episode_path = Path(str(episode["mcap_path"]))
        if episode_path.suffix.lower() == ".bvh":
            if motion_file is not None and motion_streams:
                header = read_bvh_header(episode_path)
                joint_names = [joint.name for joint in header.joints]
                parent_indices = [joint.parent_index for joint in header.joints]
                for frame in iter_bvh_motion(episode_path):
                    encoded = encode_motion_frame(frame, len(joint_names))
                    byte_offset = motion_file.tell()
                    motion_file.write(encoded)
                    offset_ns = int(frame["source_timestamp_ns"])
                    motion_index.append([offset_ns, byte_offset, len(encoded), len(motion_index)])
        else:
            messages = ()
            source = None
            if selected_topics:
                source = episode_path.open("rb")
                reader = make_reader(source)
                messages = reader.iter_messages(topics=selected_topics)
            try:
                for _schema, channel, message in messages:
                    offset_ns = max(0, int(message.log_time) - start_ns)
                    if channel.topic in camera_by_topic:
                        stream = camera_by_topic[channel.topic]
                        stream_id = str(stream["id"])
                        try:
                            compressed = decode_compressed_image(message.data)
                            output = camera_files[stream_id]
                            byte_offset = output.tell()
                            output.write(compressed.data)
                            camera_indices[stream_id].append(
                                [offset_ns, byte_offset, len(compressed.data), len(camera_indices[stream_id])]
                            )
                        except Exception as exc:
                            if len(decode_errors) < 100:
                                decode_errors.append(f"{channel.topic}: {type(exc).__name__}: {exc}")
                    elif channel.topic in motion_topics and motion_file is not None:
                        try:
                            frame, names = decode_human_motion(message.data)
                            if not joint_names:
                                joint_names = names
                                parent_indices = _parent_indices(names)
                            if names != joint_names:
                                raise ValueError("Episode 内骨架关节顺序发生变化")
                            encoded = encode_motion_frame(frame, len(joint_names))
                            byte_offset = motion_file.tell()
                            motion_file.write(encoded)
                            motion_index.append([offset_ns, byte_offset, len(encoded), len(motion_index)])
                        except Exception as exc:
                            if len(decode_errors) < 100:
                                decode_errors.append(f"{channel.topic}: {type(exc).__name__}: {exc}")
                    elif channel.topic in action_by_topic:
                        try:
                            spec = action_by_topic[channel.topic]
                            key = str(spec["key"])
                            frame = decode_robot_action(message.data, key)
                            encoded = encode_robot_action_frame(frame, key)
                            output = action_files[key]
                            byte_offset = output.tell()
                            output.write(encoded)
                            action_indices[key].append(
                                [offset_ns, byte_offset, len(encoded), len(action_indices[key])]
                            )
                        except Exception as exc:
                            if len(decode_errors) < 100:
                                decode_errors.append(f"{channel.topic}: {type(exc).__name__}: {exc}")
            finally:
                if source is not None:
                    source.close()
        for output in camera_files.values():
            output.close()
        camera_files.clear()
        if motion_file:
            motion_file.close()
            motion_file = None
        for output in action_files.values():
            output.close()
        action_files.clear()

        generated_cameras = []
        for item in camera_streams:
            stream_id = str(item["id"])
            indices = camera_indices[stream_id]
            generated_cameras.append(
                {
                    "stream_id": stream_id,
                    "topic": item["topic"],
                    "display_name": item["display_name"],
                    "message_count": len(indices),
                    "frames_file": f"cameras/{stream_id}.frames",
                    "index": indices,
                    "first_offset_ns": indices[0][0] if indices else None,
                    "last_offset_ns": indices[-1][0] if indices else None,
                }
            )
        motion = {
            "available": bool(motion_index),
            "adapter_id": "human_motion_json_v1" if motion_index else None,
            "frames_file": "mocap/motion.frames" if motion_index else None,
            "frame_encoding": MOTION_FRAME_ENCODING if motion_index else None,
            "message_count": len(motion_index),
            "index": motion_index,
            "joint_names": joint_names,
            "parent_indices": parent_indices,
            "coordinate_frame": "world",
            "units": "m",
            "first_offset_ns": motion_index[0][0] if motion_index else None,
            "last_offset_ns": motion_index[-1][0] if motion_index else None,
        }
        generated_action_sources = []
        for item in action_streams:
            spec = ROBOT_ACTION_SPECS[item["topic"]]
            key = str(spec["key"])
            indices = action_indices[key]
            generated_action_sources.append(
                {
                    "key": key,
                    "topic": item["topic"],
                    "display_name": spec["display_name"],
                    "adapter_id": spec["adapter_id"],
                    "available": bool(indices),
                    "message_count": len(indices),
                    "frames_file": f"robot_actions/{key}.frames" if indices else None,
                    "frame_encoding": ACTION_FRAME_ENCODING if indices else None,
                    "index": indices,
                    "first_offset_ns": indices[0][0] if indices else None,
                    "last_offset_ns": indices[-1][0] if indices else None,
                }
            )
        if mode == "full" and priority_manifest is not None:
            _link_manifest_frame_files(priority_manifest_path.parent, temp_dir, priority_manifest)
            priority_cameras = {item["topic"]: item for item in priority_manifest.get("cameras", [])}
            generated_camera_map = {item["topic"]: item for item in generated_cameras}
            cameras = [
                priority_cameras.get(item["topic"]) or generated_camera_map[item["topic"]]
                for item in all_camera_streams
            ]
            priority_sources = {
                item["topic"]: item
                for item in (priority_manifest.get("robot_actions") or {}).get("sources", [])
            }
            generated_source_map = {item["topic"]: item for item in generated_action_sources}
            action_sources = [
                priority_sources.get(item["topic"]) or generated_source_map[item["topic"]]
                for item in all_action_streams
            ]
            decode_errors = list(priority_manifest.get("decode_errors", [])) + decode_errors
        else:
            cameras = generated_cameras
            action_sources = generated_action_sources

        robot_actions = {
            "available": any(item["available"] for item in action_sources),
            "default_source": "policy",
            "joint_names": G1_29_JOINT_NAMES,
            "sources": action_sources,
        }
        manifest = {
            "cache_version": PLAYBACK_CACHE_VERSION,
            "episode_id": episode_id,
            "episode_name": episode["episode_name"],
            "fingerprint": episode["fingerprint"],
            "mcap_path": episode["mcap_path"],
            "start_time_ns": episode["start_time_ns"],
            "duration_ns": episode["duration_ns"],
            "created_at": _now(),
            "cache_mode": mode,
            "complete": mode == "full",
            "cameras": cameras,
            "motion": motion,
            "robot_actions": robot_actions,
            "decode_errors": decode_errors,
        }
        (temp_dir / "stream_index.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        if final_dir.exists():
            shutil.rmtree(final_dir)
        temp_dir.rename(final_dir)
        _set_cache_status(db_path, episode_id, "ready" if mode == "full" else "partial")
        return manifest | {"manifest_path": str(manifest_path), "reused": False}
    except Exception:
        for output in camera_files.values():
            output.close()
        if motion_file:
            motion_file.close()
        for output in action_files.values():
            output.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
        _set_cache_status(db_path, episode_id, "failed")
        raise


def _load_valid_cache_manifest(path: Path, fingerprint: str) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get("fingerprint") != fingerprint or manifest.get("cache_version") != PLAYBACK_CACHE_VERSION:
        return None
    return manifest


def _link_manifest_frame_files(source_root: Path, destination_root: Path, manifest: dict[str, object]) -> None:
    relative_paths = [
        item.get("frames_file")
        for item in manifest.get("cameras", [])
        if isinstance(item, dict)
    ]
    motion = manifest.get("motion") or {}
    if isinstance(motion, dict):
        relative_paths.append(motion.get("frames_file"))
    robot_actions = manifest.get("robot_actions") or {}
    if isinstance(robot_actions, dict):
        relative_paths.extend(
            item.get("frames_file")
            for item in robot_actions.get("sources", [])
            if isinstance(item, dict)
        )
    for relative_path in relative_paths:
        if not relative_path:
            continue
        source = source_root / str(relative_path)
        destination = destination_root / str(relative_path)
        if not source.is_file() or destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)


def decode_human_motion(payload: bytes) -> tuple[dict[str, object], list[str]]:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schema") != "mocap_human_motion.raw_v1":
        raise ValueError("不支持的 Mocap schema")
    motion = value.get("motion")
    if not isinstance(motion, dict) or motion.get("format") != "link_pose_float32":
        raise ValueError("不支持的 Mocap motion.format")
    names = list(motion.get("link_order") or [])
    links = motion.get("links") or {}
    if not names or not isinstance(links, dict):
        raise ValueError("Mocap 消息缺少 link_order/links")
    positions: list[list[float]] = []
    rotations: list[list[float]] = []
    validity: list[bool] = []
    for name in names:
        pose = links.get(name) if isinstance(links.get(name), dict) else {}
        position = pose.get("position")
        quaternion = pose.get("quat_wxyz")
        valid = (
            isinstance(position, list)
            and len(position) == 3
            and isinstance(quaternion, list)
            and len(quaternion) == 4
        )
        validity.append(valid)
        positions.append([float(item) for item in position] if valid else [0.0, 0.0, 0.0])
        rotations.append([float(item) for item in quaternion] if valid else [1.0, 0.0, 0.0, 0.0])
    return {
        "positions": positions,
        "rotations_wxyz": rotations,
        "validity": validity,
        "source_timestamp_ns": value.get("source_timestamp_ns"),
        "sequence": value.get("sequence"),
    }, names


def decode_robot_action(payload: bytes, source_key: str) -> dict[str, object]:
    value = decode_messagepack(payload)
    if not isinstance(value, dict):
        raise ValueError("机器人动作消息不是对象")

    root_position = None
    root_quaternion = None
    if source_key == "policy":
        if value.get("schema") != "g1_policy_controller_context.v1":
            raise ValueError("不支持的 Policy controller context schema")
        context = value.get("context")
        if not isinstance(context, dict):
            raise ValueError("Policy controller context 消息缺少 context")
        positions = context.get("body_q")
        if context.get("base_quat") is not None:
            root_quaternion = _finite_float_list(context.get("base_quat"), 4, "Policy base quaternion")
    elif source_key == "policy_target":
        if value.get("schema") != "g1_policy_input_ref_motion_cmd.v1":
            raise ValueError("不支持的 PMG 参考动作 schema")
        command = value.get("cmd")
        if not isinstance(command, dict):
            raise ValueError("PMG 参考动作消息缺少 cmd")
        isaaclab_positions = _finite_float_list(
            command.get("qpos") or command.get("motion_joint_positions"),
            29,
            "PMG joint positions",
        )
        positions = [isaaclab_positions[index] for index in G1_MUJOCO_TO_ISAACLAB_INDICES]
        if command.get("body_pos") is not None:
            root_position = _first_finite_vector(command.get("body_pos"), 3, "PMG root position")
        if command.get("body_quat") is not None:
            root_quaternion = _first_finite_vector(command.get("body_quat"), 4, "PMG root quaternion")
    elif source_key == "policy_command":
        if value.get("schema") != "g1_policy_final_action.v1":
            raise ValueError("不支持的 Policy action schema")
        action = value.get("action")
        if not isinstance(action, dict):
            raise ValueError("Policy action 消息缺少 action")
        positions = action.get("final_q_target")
    elif source_key == "soma":
        if value.get("schema") != "soma_retarget_action.v1":
            raise ValueError("不支持的 SOMA action schema")
        action = value.get("action")
        if not isinstance(action, dict):
            raise ValueError("SOMA action 消息缺少 action")
        qpos = action.get("qpos")
        if not isinstance(qpos, list) or len(qpos) != 36:
            raise ValueError("SOMA qpos 必须包含根姿态和 29 个关节")
        root_position = _finite_float_list(qpos[:3], 3, "SOMA root position")
        root_quaternion = _finite_float_list(qpos[3:7], 4, "SOMA root quaternion")
        quaternion_order = str(action.get("root_quat_order") or "wxyz").lower()
        if quaternion_order == "xyzw":
            root_quaternion = [root_quaternion[3], *root_quaternion[:3]]
        elif quaternion_order != "wxyz":
            raise ValueError(f"不支持的 SOMA root quaternion 顺序: {quaternion_order}")
        positions = qpos[7:]
    else:
        raise ValueError(f"不支持的机器人动作源: {source_key}")

    frame: dict[str, object] = {
        "source_key": source_key,
        "joint_positions": _finite_float_list(positions, 29, "G1 joint positions"),
        "source_timestamp_ns": value.get("source_timestamp_ns"),
        "sequence": value.get("sequence"),
    }
    if root_position is not None:
        frame["root_position"] = root_position
    if root_quaternion is not None:
        frame["root_quaternion_wxyz"] = root_quaternion
    return frame


def _finite_float_list(value: object, size: int, field_name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{field_name} 必须包含 {size} 个数值")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{field_name} 包含非有限值")
    return result


def _first_finite_vector(value: object, size: int, field_name: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} 必须至少包含一个 {size} 维向量")
    first = value[0] if isinstance(value[0], list) else value[:size]
    return _finite_float_list(first, size, field_name)


@lru_cache(maxsize=8)
def _motion_frame_struct(joint_count: int) -> struct.Struct:
    if joint_count <= 0:
        raise ValueError("Mocap 关节数量必须为正数")
    return struct.Struct(f"<qq{joint_count * 7}f{joint_count}B")


@lru_cache(maxsize=1)
def _action_frame_struct() -> struct.Struct:
    # timestamp, sequence, root-field flags, 29 joints, optional root xyz + quaternion.
    # Optional fields keep a fixed-width frame so all action sources share one decoder.
    return struct.Struct("<qqI29f3f4f")


def _optional_int64(value: object) -> int:
    return -1 if value is None else int(value)


def encode_motion_frame(frame: dict[str, object], joint_count: int) -> bytes:
    positions = frame.get("positions") or []
    rotations = frame.get("rotations_wxyz") or []
    validity = frame.get("validity") or []
    if len(positions) != joint_count or len(rotations) != joint_count or len(validity) != joint_count:
        raise ValueError("Mocap 二进制帧的关节数量不一致")
    coordinates = [float(component) for position in positions for component in position]
    coordinates.extend(float(component) for rotation in rotations for component in rotation)
    return _motion_frame_struct(joint_count).pack(
        _optional_int64(frame.get("source_timestamp_ns")),
        _optional_int64(frame.get("sequence")),
        *coordinates,
        *(1 if item else 0 for item in validity),
    )


def decode_motion_frame(payload: bytes, joint_count: int) -> dict[str, object]:
    values = _motion_frame_struct(joint_count).unpack(payload)
    timestamp_ns, sequence = values[:2]
    coordinate_end = 2 + joint_count * 7
    coordinates = values[2:coordinate_end]
    positions = [list(coordinates[index * 3 : index * 3 + 3]) for index in range(joint_count)]
    rotation_start = joint_count * 3
    rotations = [
        list(coordinates[rotation_start + index * 4 : rotation_start + index * 4 + 4])
        for index in range(joint_count)
    ]
    return {
        "positions": positions,
        "rotations_wxyz": rotations,
        "validity": [bool(value) for value in values[coordinate_end:]],
        "source_timestamp_ns": None if timestamp_ns == -1 else timestamp_ns,
        "sequence": None if sequence == -1 else sequence,
    }


def encode_robot_action_frame(frame: dict[str, object], source_key: str) -> bytes:
    if source_key not in ROBOT_ACTION_KEYS:
        raise ValueError(f"不支持的机器人动作源: {source_key}")
    root_position = frame.get("root_position")
    root_quaternion = frame.get("root_quaternion_wxyz")
    flags = 0
    if root_position is not None:
        flags |= ACTION_ROOT_POSITION
    if root_quaternion is not None:
        flags |= ACTION_ROOT_QUATERNION
    values = [
        _optional_int64(frame.get("source_timestamp_ns")),
        _optional_int64(frame.get("sequence")),
        flags,
        *_finite_float_list(frame.get("joint_positions"), 29, "G1 joint positions"),
        *(_finite_float_list(root_position, 3, "G1 root position") if root_position is not None else [0.0] * 3),
        *(
            _finite_float_list(root_quaternion, 4, "G1 root quaternion")
            if root_quaternion is not None
            else [1.0, 0.0, 0.0, 0.0]
        ),
    ]
    return _action_frame_struct().pack(*values)


def decode_robot_action_frame(payload: bytes, source_key: str) -> dict[str, object]:
    if source_key not in ROBOT_ACTION_KEYS:
        raise ValueError(f"不支持的机器人动作源: {source_key}")
    values = _action_frame_struct().unpack(payload)
    timestamp_ns, sequence, flags = values[:3]
    if flags & ~(ACTION_ROOT_POSITION | ACTION_ROOT_QUATERNION):
        raise ValueError(f"机器人动作帧包含未知根位姿标志: {flags}")
    frame: dict[str, object] = {
        "source_key": source_key,
        "joint_positions": list(values[3:32]),
        "source_timestamp_ns": None if timestamp_ns == -1 else timestamp_ns,
        "sequence": None if sequence == -1 else sequence,
    }
    if flags & ACTION_ROOT_POSITION:
        frame["root_position"] = list(values[32:35])
    if flags & ACTION_ROOT_QUATERNION:
        frame["root_quaternion_wxyz"] = list(values[35:39])
    return frame


def _parent_indices(names: list[str]) -> list[int]:
    lookup = {name: index for index, name in enumerate(names)}
    return [lookup.get(HUMAN_PARENT_NAMES.get(name, ""), -1) for name in names]


def public_cache_manifest(manifest: dict[str, object]) -> dict[str, object]:
    cameras = [
        {key: value for key, value in camera.items() if key != "index"}
        for camera in manifest.get("cameras", [])
    ]
    motion = {key: value for key, value in (manifest.get("motion") or {}).items() if key != "index"}
    robot_actions = dict(manifest.get("robot_actions") or {})
    robot_actions["sources"] = [
        {key: value for key, value in source.items() if key != "index"}
        for source in robot_actions.get("sources", [])
    ]
    return {key: value for key, value in manifest.items() if key not in {"cameras", "motion", "robot_actions"}} | {
        "cameras": cameras,
        "motion": motion,
        "robot_actions": robot_actions,
    }


def read_cached_camera_frame(
    manifest_path: str | Path,
    stream_id: str,
    time_ns: int,
) -> dict[str, object]:
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    camera = next((item for item in manifest["cameras"] if item["stream_id"] == stream_id), None)
    if not camera or not camera["index"]:
        raise KeyError(f"相机缓存不存在: {stream_id}")
    entry = _nearest_entry(camera["index"], time_ns)
    path = manifest_file.parent / camera["frames_file"]
    with path.open("rb") as source:
        source.seek(entry[1])
        jpeg = source.read(entry[2])
    return {
        "jpeg": jpeg,
        "frame_offset_ns": entry[0],
        "skew_ns": entry[0] - int(time_ns),
        "frame_index": entry[3],
        "end_of_stream": entry[3] == len(camera["index"]) - 1,
    }


def read_cached_motion_frame(manifest_path: str | Path, time_ns: int) -> dict[str, object]:
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    motion = manifest.get("motion") or {}
    if not motion.get("available") or not motion.get("index"):
        raise KeyError("Mocap 缓存不可用")
    entry = _nearest_entry(motion["index"], time_ns)
    path = manifest_file.parent / motion["frames_file"]
    with path.open("rb") as source:
        source.seek(entry[1])
        encoded = source.read(entry[2])
    if motion.get("frame_encoding") == MOTION_FRAME_ENCODING:
        payload = decode_motion_frame(encoded, len(motion["joint_names"]))
    else:
        payload = json.loads(encoded)
    return payload | {
        "frame_offset_ns": entry[0],
        "skew_ns": entry[0] - int(time_ns),
        "frame_index": entry[3],
        "joint_names": motion["joint_names"],
        "parent_indices": motion["parent_indices"],
        "coordinate_frame": motion.get("coordinate_frame"),
        "units": motion.get("units"),
    }


def read_cached_robot_action_frame(
    manifest_path: str | Path,
    source_key: str,
    time_ns: int,
) -> dict[str, object]:
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    robot_actions = manifest.get("robot_actions") or {}
    source = next(
        (item for item in robot_actions.get("sources", []) if item.get("key") == source_key),
        None,
    )
    if not source or not source.get("available") or not source.get("index"):
        raise KeyError(f"机器人动作缓存不可用: {source_key}")
    entry = _nearest_entry(source["index"], time_ns)
    path = manifest_file.parent / source["frames_file"]
    with path.open("rb") as input_file:
        input_file.seek(entry[1])
        encoded = input_file.read(entry[2])
    if source.get("frame_encoding") == ACTION_FRAME_ENCODING:
        payload = decode_robot_action_frame(encoded, source_key)
    else:
        payload = json.loads(encoded)
    return payload | {
        "frame_offset_ns": entry[0],
        "skew_ns": entry[0] - int(time_ns),
        "frame_index": entry[3],
        "end_of_stream": entry[3] == len(source["index"]) - 1,
        "joint_names": robot_actions["joint_names"],
    }


def _nearest_entry(entries: list[list[int]], time_ns: int) -> list[int]:
    timestamps = [entry[0] for entry in entries]
    position = bisect.bisect_left(timestamps, int(time_ns))
    if position <= 0:
        return entries[0]
    if position >= len(entries):
        return entries[-1]
    before = entries[position - 1]
    after = entries[position]
    return before if int(time_ns) - before[0] <= after[0] - int(time_ns) else after


def _set_cache_status(db_path: str | Path, episode_id: str, status: str) -> None:
    with connect_workspace(db_path) as connection:
        connection.execute("UPDATE episode SET cache_status = ?, updated_at = ? WHERE id = ?", (status, _now(), episode_id))
